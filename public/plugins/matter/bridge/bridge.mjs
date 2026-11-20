/* ComfyOS Matter helper – matter.js as bridge and controller, driven by the plugin over stdin/stdout.

   The Matter plugin (public/plugins/matter) starts this file with Node, locked in:
       node --permission --allow-fs-read=<dist/bridge.mjs> <dist/bridge.mjs>
   It cannot read or write files, start processes or load native code. Everything it knows comes from the
   plugin, and everything it wants kept goes back to the plugin:

     stdin   one JSON object per line from the plugin  (storage, start, endpoints, update, commissioning,
             remove_fabric, pair, unpair, invoke, reply)
     stdout  one JSON object per line to the plugin    (ready, storage, started, pairing, fabrics, command,
             node, node_removed, attribute, result, error)
     stderr  silent (ComfyOS discards it). Set COMFYOS_MATTER_LOG=notice when running by hand to see the log.

   Two Matter nodes live in this process:
     bridge      a server node with an Aggregator (endpoint 1). Every device ComfyOS shares is a Bridged Node
                 below it. Controllers such as Alexa, Apple Home or Google Home commission this node. A command
                 from a controller is never applied here – it is sent to the plugin as `command` and only takes
                 effect when the plugin answers `reply` with "ok". The plugin enforces locks and the share list.
     controller  a node with its own fabric that commissions other Matter devices over the network (`pair`),
                 reports them as `node`/`attribute` and switches them (`invoke`).

   Values on this channel are already in Matter units (level 1–254, mireds, 0.01 °C, 0.01 %); the plugin's
   devices.py converts between ComfyOS and Matter. Messages carry no free text from matter.js: errors are
   reported as short codes only.

   The helper ends when stdin closes – that is the contract with ComfyOS, so nothing is left behind. */
import './setup.mjs'

import { createInterface } from 'node:readline'

import {
  ControllerBehavior, Endpoint, Environment, Logger, MemoryStorageDriver, ServerNode, StorageService, VendorId, fromJson,
  toJson
} from '@matter/main'
import {
  BridgedDeviceBasicInformationServer, OccupancySensingServer, SmokeCoAlarmServer, ThermostatServer, WindowCoveringServer
} from '@matter/main/behaviors'
import {
  ColorTemperatureLightDevice, ContactSensorDevice, DimmableLightDevice, ExtendedColorLightDevice,
  HumiditySensorDevice, OccupancySensorDevice, OnOffLightDevice, OnOffPlugInUnitDevice, SmokeCoAlarmDevice,
  TemperatureSensorDevice, ThermostatDevice, WindowCoveringDevice
} from '@matter/main/devices'
import { AggregatorEndpoint } from '@matter/main/endpoints'
import { DeviceCommissioner, FabricManager } from '@matter/main/protocol'
import { FabricIndex, QrCode, StatusCode, StatusResponseError } from '@matter/main/types'

// ---------------------------------------------------------------------------------------------------------
// Limits and constants
// ---------------------------------------------------------------------------------------------------------

const PROTOCOL = 1                       // version of the line protocol; the plugin checks it in `ready`
const MAX_LINE = 1024 * 1024             // bytes; ComfyOS ends a helper that prints a longer line
const STORAGE_CHUNK = 200 * 1024         // bytes of storage operations per `storage` message
const STORAGE_FLUSH_MS = 25              // collect storage writes this long before sending them
const REPLY_TIMEOUT_MS = 8000            // how long a controller's command waits for the plugin's answer
const NODE_REPORT_MS = 150               // collect attribute changes of a controller device this long
const SHUTDOWN_LIMIT_MS = 3000           // after stdin closed, exit at the latest after this time
const AGGREGATOR_NUMBER = 1
const COLOR_MODE_XY = 1                  // ColorControl.ColorMode.CurrentXAndCurrentY
const COLOR_MODE_TEMPERATURE = 2         // ColorControl.ColorMode.ColorTemperatureMireds
const SYSTEM_MODE_HEAT = 4               // Thermostat.SystemMode.Heat
const HEATING_ONLY = 2                   // Thermostat.ControlSequenceOfOperation.HeatingOnly
const SMOKE_NORMAL = 0
const SMOKE_CRITICAL = 2
const EXPRESSED_NORMAL = 0
const EXPRESSED_SMOKE = 1

// ---------------------------------------------------------------------------------------------------------
// Talking to the plugin
// ---------------------------------------------------------------------------------------------------------

/* Write one message. A message that would exceed the line limit is replaced by an error code. */
function send (message) {
  let line = JSON.stringify(message)
  if (line.length > MAX_LINE / 2) {
    line = JSON.stringify({ type: 'error', code: 'message_too_large' })
  }
  process.stdout.write(line + '\n')
}

/* Report a problem as a code. The text of an exception never leaves the helper; it may hold foreign input. */
function sendError (code, error) {
  if (debugLog && error) process.stderr.write(`${code}: ${error && error.stack ? error.stack : error}\n`)
  send({ type: 'error', code })
}

/* Short code for an exception: its class name, reduced to letters. */
function errorCode (error) {
  const name = error && error.constructor && error.constructor.name ? error.constructor.name : 'Error'
  return name.replace(/[^A-Za-z0-9]/g, '').slice(0, 60) || 'Error'
}

const debugLog = process.env.COMFYOS_MATTER_LOG || ''
Logger.level = debugLog || 'fatal'
Logger.log = (level, text) => { if (debugLog) process.stderr.write(text + '\n') }

// ---------------------------------------------------------------------------------------------------------
// Storage: kept in memory here, persisted by the plugin
// ---------------------------------------------------------------------------------------------------------

const stores = {}                        // namespace ("bridge", "controller") -> { context: { key: value } }
let pendingOps = []                      // storage operations not sent yet: [namespace, op, context, key, json]
let flushTimer = null

/* Queue one storage operation and send the queue shortly after, so a burst of writes becomes few messages. */
function recordOp (namespace, op, context, key, value) {
  pendingOps.push([namespace, op, context, key === undefined ? null : key, value === undefined ? null : toJson(value)])
  if (flushTimer === null) flushTimer = setTimeout(flushStorage, STORAGE_FLUSH_MS)
}

/* Send the queued storage operations in order, split into messages below the line limit. */
function flushStorage () {
  flushTimer = null
  let chunk = []
  let size = 0
  for (const op of pendingOps) {
    const opSize = (op[4] ? op[4].length : 0) + (op[2] ? op[2].length : 0) + 80
    if (chunk.length && size + opSize > STORAGE_CHUNK) {
      send({ type: 'storage', ops: chunk })
      chunk = []
      size = 0
    }
    chunk.push(op)
    size += opSize
  }
  if (chunk.length) send({ type: 'storage', ops: chunk })
  pendingOps = []
}

/* matter.js storage driver: values live in `stores`; every change is also reported to the plugin. */
class PluginStorage extends MemoryStorageDriver {
  static id = 'comfyos'

  static create (namespace) {
    const name = String(namespace && namespace.namespace !== undefined ? namespace.namespace : namespace)
    const storage = new PluginStorage(name)
    storage.initialize()
    return storage
  }

  constructor (name) {
    super(stores[name] || (stores[name] = {}))
    this.namespaceName = name
  }

  set (contexts, keyOrValues, value) {
    super.set(contexts, keyOrValues, value)
    const context = this.createContextKey(contexts)
    if (typeof keyOrValues === 'string') {
      recordOp(this.namespaceName, 'set', context, keyOrValues, value)
    } else {
      for (const [key, entry] of Object.entries(keyOrValues)) recordOp(this.namespaceName, 'set', context, key, entry)
    }
  }

  delete (contexts, key) {
    super.delete(contexts, key)
    recordOp(this.namespaceName, 'delete', this.createContextKey(contexts), key)
  }

  clearAll (contexts) {
    super.clearAll(contexts)
    recordOp(this.namespaceName, 'clear', this.createContextKey(contexts))
  }
}

/* Take over stored values the plugin sends before `start`: { namespace, values: { context: { key: json } } }. */
function loadStorage (message) {
  const name = String(message.namespace)
  const store = stores[name] || (stores[name] = {})
  for (const [context, values] of Object.entries(message.values || {})) {
    const target = store[context] || (store[context] = {})
    for (const [key, json] of Object.entries(values)) target[key] = fromJson(json)
  }
}

const storageService = Environment.default.get(StorageService)
storageService.registerDriver(PluginStorage)
storageService.defaultDriver = PluginStorage.id

// ---------------------------------------------------------------------------------------------------------
// Bridge: commands from controllers go to the plugin first
// ---------------------------------------------------------------------------------------------------------

let bridge = null                        // the bridge ServerNode
let aggregator = null
const shared = new Map()                 // endpoint number -> { endpoint, kind, signature }
const waiting = new Map()                // command id -> resolve function, until the plugin's `reply` arrives
const asking = new Set()                 // endpoint numbers with a command on its way (suppresses nested asks)
const applying = new Set()               // endpoint numbers the helper is writing plugin values to right now
const lockedNumbers = new Set()          // endpoint numbers of devices the user locked in ComfyOS
let commandSeq = 0

/* Ask the plugin to carry out what a controller wants and wait for its answer. Throws a Matter status error
   unless the plugin says "ok" – then the controller sees a failure and nothing changes here.

   Behaviors call each other (a level command also switches on); only the outermost call of an endpoint asks. */
async function ask (behavior, state) {
  const fabric = behavior.context && behavior.context.fabric !== undefined ? Number(behavior.context.fabric) : null
  return askPlugin(behavior.endpoint.number, fabric, state)
}

/* The same for a known endpoint number and fabric (used where no behavior is at hand any more). */
async function askPlugin (number, fabric, state) {
  if (asking.has(number) || applying.has(number)) return
  const id = ++commandSeq
  asking.add(number)
  try {
    const answer = await new Promise(resolve => {
      const timer = setTimeout(() => { waiting.delete(id); resolve('timeout') }, REPLY_TIMEOUT_MS)
      waiting.set(id, status => { clearTimeout(timer); resolve(status) })
      send({ type: 'command', id, number, state, fabric })
    })
    if (answer !== 'ok') {
      throw new StatusResponseError('refused', answer === 'timeout' ? StatusCode.Timeout : StatusCode.Failure)
    }
  } finally {
    asking.delete(number)
  }
}

/* Run the default logic of matter.js for an accepted command without asking again. */
async function accepted (behavior, run) {
  const number = behavior.endpoint.number
  const nested = asking.has(number)
  asking.add(number)
  try {
    return await run()
  } finally {
    if (!nested) asking.delete(number)
  }
}

/* On/Off: on, off and toggle are sent to the plugin. */
function bridgedOnOff (Base) {
  return class BridgedOnOffServer extends Base {
    async on () {
      await ask(this, { on: true })
      return accepted(this, () => super.on())
    }

    async off () {
      await ask(this, { on: false })
      return accepted(this, () => super.off())
    }

    async toggle () {
      await ask(this, { on: !this.state.onOff })
      return accepted(this, () => super.toggle())
    }
  }
}

/* Level Control: every way to change the level ends in one target level for the plugin; no transitions here. */
function bridgedLevel (Base) {
  return class BridgedLevelControlServer extends Base {
    async moveToLevelLogic (level, transitionTime, withOnOff, options) {
      const state = { level }
      if (withOnOff) state.on = level > (this.state.minLevel || 1)
      await ask(this, state)
      return accepted(this, () => super.moveToLevelLogic(level, null, withOnOff, options))
    }

    async moveLogic (moveMode, rate, withOnOff, options) {
      const target = moveMode === 0 ? (this.state.maxLevel || 254) : (this.state.minLevel || 1)
      return this.moveToLevelLogic(target, null, withOnOff, options)
    }

    async stepLogic (stepMode, stepSize, transitionTime, withOnOff, options) {
      const current = this.state.currentLevel === null || this.state.currentLevel === undefined ? 1 : this.state.currentLevel
      const wanted = stepMode === 0 ? current + stepSize : current - stepSize
      const target = Math.max(this.state.minLevel || 1, Math.min(this.state.maxLevel || 254, wanted))
      return this.moveToLevelLogic(target, null, withOnOff, options)
    }

    stopLogic () {}
  }
}

/* Color Control: white tone in mireds and colour as xy; nothing moves gradually. */
function bridgedColor (Base) {
  return class BridgedColorControlServer extends Base {
    async moveToColorTemperatureLogic (targetMireds, transitionTime) {
      await ask(this, { mireds: targetMireds })
      return accepted(this, () => super.moveToColorTemperatureLogic(targetMireds, 0))
    }

    async moveToColorLogic (targetX, targetY, transitionTime) {
      await ask(this, { x: targetX, y: targetY })
      return accepted(this, () => super.moveToColorLogic(targetX, targetY, 0))
    }
  }
}

/* Window Covering: a target position, or just a direction, or stop. */
function bridgedCovering (Base) {
  return class BridgedWindowCoveringServer extends Base {
    async handleMovement (type, reversed, direction, targetPercent100ths) {
      if (targetPercent100ths !== undefined && targetPercent100ths !== null) {
        await ask(this, { position: targetPercent100ths })
      } else {
        await ask(this, { cover: direction === 1 ? 'up' : 'down' })     // MovementDirection.Open = 1
      }
    }

    async handleStopMovement () {
      await ask(this, { cover: 'stop' })
    }
  }
}

/* Thermostat: the set point is an attribute a controller writes, not a command – and matter.js decides about a
   write without waiting. So a write to a locked device is refused at once (the plugin tells the helper which
   devices are locked), every other write is accepted and then sent to the plugin. If the plugin refuses after
   all, the old set point is written back. */
function bridgedThermostat (Base) {
  return class BridgedThermostatServer extends Base {
    initialize () {
      const result = super.initialize()
      this.reactTo(this.events.occupiedHeatingSetpoint$Changing, this.setpointChanging)
      this.reactTo(this.events.occupiedHeatingSetpoint$Changed, this.setpointChanged)
      this.reactTo(this.events.systemMode$Changing, this.modeChanging)
      return result
    }

    setpointChanging (value, oldValue, context) {
      const number = this.endpoint.number
      if (context === undefined || context.offline || applying.has(number)) return
      if (lockedNumbers.has(number)) throw new StatusResponseError('locked', StatusCode.Failure)
    }

    setpointChanged (value, oldValue, context) {
      const number = this.endpoint.number
      if (context === undefined || context.offline || applying.has(number)) return
      const fabric = context.fabric !== undefined ? Number(context.fabric) : null
      askPlugin(number, fabric, { heating_setpoint: value }).catch(() => {
        queue = queue.then(() => applyShared(number, { thermostat: { occupiedHeatingSetpoint: oldValue } }))
          .catch(failure => sendError('revert_failed', failure))
      })
    }

    modeChanging (value, oldValue, context) {
      if (context === undefined || context.offline || applying.has(this.endpoint.number)) return
      if (value !== SYSTEM_MODE_HEAT) throw new StatusResponseError('heat only', StatusCode.ConstraintError)
    }
  }
}

/* Endpoint type for each kind the plugin can share: device type, behaviors with features, command handling. */
const KINDS = {
  on_off_light: () => withBridged(OnOffLightDevice, { onOff: bridgedOnOff }),
  on_off_plug: () => withBridged(OnOffPlugInUnitDevice, { onOff: bridgedOnOff }),
  dimmable_light: () => withBridged(DimmableLightDevice, { onOff: bridgedOnOff, levelControl: bridgedLevel }),
  color_temperature_light: () => withBridged(ColorTemperatureLightDevice,
    { onOff: bridgedOnOff, levelControl: bridgedLevel, colorControl: bridgedColor }),
  extended_color_light: () => withBridged(ExtendedColorLightDevice,
    { onOff: bridgedOnOff, levelControl: bridgedLevel, colorControl: bridgedColor }, { colorControl: ['Xy', 'ColorTemperature'] }),
  thermostat: () => withBridged(ThermostatDevice, { thermostat: bridgedThermostat }, { thermostat: ['Heating'] }),
  window_covering: () => withBridged(WindowCoveringDevice, { windowCovering: bridgedCovering },
    { windowCovering: ['Lift', 'PositionAwareLift'] }),
  contact_sensor: () => withBridged(ContactSensorDevice, {}),
  occupancy_sensor: () => withBridged(OccupancySensorDevice, {}, { occupancySensing: ['PassiveInfrared'] }),
  temperature_sensor: () => withBridged(TemperatureSensorDevice, {}),
  humidity_sensor: () => withBridged(HumiditySensorDevice, {}),
  smoke_alarm: () => withBridged(SmokeCoAlarmDevice, {}, { smokeCoAlarm: ['SmokeAlarm'] })
}
const kindTypes = {}                     // kind -> endpoint type, built once
/* Clusters whose features must be chosen before they can be used: these device types bring no default server. */
const FEATURE_SERVERS = {
  thermostat: ThermostatServer,
  windowCovering: WindowCoveringServer,
  occupancySensing: OccupancySensingServer,
  smokeCoAlarm: SmokeCoAlarmServer
}

/* Build an endpoint type: the device type plus Bridged Device Basic Information, each listed behavior of the
   device type narrowed to `features` and wrapped by its command handler. */
function withBridged (deviceType, handlers, features = {}) {
  const behaviors = [BridgedDeviceBasicInformationServer]
  const names = new Set([...Object.keys(handlers), ...Object.keys(features)])
  for (const name of names) {
    let base = deviceType.behaviors[name] || FEATURE_SERVERS[name]
    if (features[name]) base = base.with(...features[name])
    behaviors.push(handlers[name] ? handlers[name](base) : base)
  }
  return deviceType.with(...behaviors)
}

/* Endpoint type for a kind, or undefined for a kind this helper does not know. */
function typeOf (kind) {
  if (!Object.prototype.hasOwnProperty.call(KINDS, kind)) return undefined
  if (!kindTypes[kind]) kindTypes[kind] = KINDS[kind]()
  return kindTypes[kind]
}

/* Translate plugin values (Matter units) into a matter.js state patch for one endpoint. `create` adds the
   values a cluster needs once, when the endpoint is built. */
function patchFor (kind, state, create) {
  const patch = {}
  const cluster = name => patch[name] || (patch[name] = {})
  if (state.on !== undefined) cluster('onOff').onOff = Boolean(state.on)
  if (state.level !== undefined) cluster('levelControl').currentLevel = state.level
  if (kind === 'color_temperature_light' || kind === 'extended_color_light') {
    const color = cluster('colorControl')
    if (create) {
      color.colorTempPhysicalMinMireds = state.min_mireds || 153
      color.colorTempPhysicalMaxMireds = state.max_mireds || 500
      color.coupleColorTempToLevelMinMireds = color.colorTempPhysicalMinMireds
      color.startUpColorTemperatureMireds = null
    }
    if (state.mireds !== undefined) color.colorTemperatureMireds = state.mireds
    if (state.x !== undefined && kind === 'extended_color_light') color.currentX = state.x
    if (state.y !== undefined && kind === 'extended_color_light') color.currentY = state.y
    const mode = state.color_mode === 'xy' && kind === 'extended_color_light' ? COLOR_MODE_XY
      : state.color_mode === 'ct' ? COLOR_MODE_TEMPERATURE : undefined
    if (mode !== undefined) {
      color.colorMode = mode
      color.enhancedColorMode = mode
    } else if (create) {
      color.colorMode = COLOR_MODE_TEMPERATURE
      color.enhancedColorMode = COLOR_MODE_TEMPERATURE
    }
    if (Object.keys(color).length === 0) delete patch.colorControl
  }
  if (kind === 'thermostat') {
    const thermostat = cluster('thermostat')
    if (create) {
      thermostat.systemMode = SYSTEM_MODE_HEAT
      thermostat.controlSequenceOfOperation = HEATING_ONLY
      thermostat.absMinHeatSetpointLimit = state.min_heat || 500
      thermostat.absMaxHeatSetpointLimit = state.max_heat || 3000
      thermostat.minHeatSetpointLimit = thermostat.absMinHeatSetpointLimit
      thermostat.maxHeatSetpointLimit = thermostat.absMaxHeatSetpointLimit
    }
    if (state.local_temperature !== undefined) thermostat.localTemperature = state.local_temperature
    if (state.heating_setpoint !== undefined) thermostat.occupiedHeatingSetpoint = state.heating_setpoint
    if (Object.keys(thermostat).length === 0) delete patch.thermostat
  }
  if (kind === 'window_covering') {
    const covering = cluster('windowCovering')
    if (create) {
      covering.type = 0                  // Rollershade
      covering.endProductType = 0        // RollerShade
      covering.configStatus = { operational: true, onlineReserved: true, liftMovementReversed: false, liftPositionAware: true }
    }
    if (state.position !== undefined) {
      covering.currentPositionLiftPercent100ths = state.position
      covering.targetPositionLiftPercent100ths = state.position
      covering.currentPositionLiftPercentage = state.position === null ? null : Math.round(state.position / 100)
    }
    if (Object.keys(covering).length === 0) delete patch.windowCovering
  }
  if (state.contact !== undefined) cluster('booleanState').stateValue = Boolean(state.contact)
  if (state.occupied !== undefined) cluster('occupancySensing').occupancy = { occupied: Boolean(state.occupied) }
  if (kind === 'occupancy_sensor' && create) {
    cluster('occupancySensing').occupancySensorType = 0                              // PIR
    cluster('occupancySensing').occupancySensorTypeBitmap = { pir: true, ultrasonic: false, physicalContact: false }
  }
  if (state.temperature !== undefined) cluster('temperatureMeasurement').measuredValue = state.temperature
  if (state.humidity !== undefined) cluster('relativeHumidityMeasurement').measuredValue = state.humidity
  if (kind === 'smoke_alarm') {
    const alarm = cluster('smokeCoAlarm')
    if (create) {
      alarm.batteryAlert = 0
      alarm.testInProgress = false
      alarm.hardwareFaultAlert = false
      alarm.endOfServiceAlert = 0
    }
    if (state.smoke !== undefined || create) {
      alarm.smokeState = state.smoke ? SMOKE_CRITICAL : SMOKE_NORMAL
      alarm.expressedState = state.smoke ? EXPRESSED_SMOKE : EXPRESSED_NORMAL
    }
  }
  return patch
}

/* The Bridged Device Basic Information of a shared device. */
function basicInfo (entry) {
  const name = String(entry.name || 'Gerät').slice(0, 32)
  return {
    nodeLabel: name,
    productName: name,
    productLabel: name,
    vendorName: 'ComfyOS',
    serialNumber: String(entry.serial || `comfyos-${entry.number}`).slice(0, 32),
    uniqueId: `share-${entry.number}`,
    reachable: entry.reachable !== false
  }
}

/* Create the Bridged Node for one shared device below the aggregator. */
async function addShared (entry) {
  const type = typeOf(entry.kind)
  if (type === undefined) return sendError('unknown_kind')
  const options = {
    id: `share-${entry.number}`,
    number: entry.number,
    bridgedDeviceBasicInformation: basicInfo(entry),
    ...patchFor(entry.kind, entry.state || {}, true)
  }
  const endpoint = new Endpoint(type, options)
  await aggregator.add(endpoint)
  shared.set(entry.number, { endpoint, kind: entry.kind, signature: signatureOf(entry) })
}

/* Remember whether the user locked a shared device (only needed where a write cannot wait for the plugin). */
function noteLock (number, locked) {
  if (locked) lockedNumbers.add(number)
  else lockedNumbers.delete(number)
}

/* What makes an endpoint a different endpoint: kind and the values fixed at creation. */
function signatureOf (entry) {
  const state = entry.state || {}
  return [entry.kind, state.min_mireds, state.max_mireds, state.min_heat, state.max_heat].join('|')
}

/* Write plugin values to an endpoint without treating them as a controller's command. */
async function applyShared (number, patch) {
  const record = shared.get(number)
  if (!record || Object.keys(patch).length === 0) return
  applying.add(number)
  try {
    await record.endpoint.set(patch)
  } finally {
    applying.delete(number)
  }
}

/* Bring the Bridged Nodes in line with the plugin's share list: add, remove, rename, refresh values. */
async function syncEndpoints (entries) {
  const wanted = new Map()
  for (const entry of entries) wanted.set(entry.number, entry)
  for (const [number, record] of [...shared]) {
    const entry = wanted.get(number)
    if (entry === undefined || signatureOf(entry) !== record.signature) {
      shared.delete(number)
      lockedNumbers.delete(number)
      await record.endpoint.close()
    }
  }
  for (const entry of entries) {
    noteLock(entry.number, entry.locked)
    if (!shared.has(entry.number)) {
      await addShared(entry)
    } else {
      await applyShared(entry.number, { bridgedDeviceBasicInformation: basicInfo(entry), ...patchFor(entry.kind, entry.state || {}, false) })
    }
  }
}

/* One device changed: write its new values. */
async function updateShared (message) {
  const record = shared.get(message.number)
  if (!record) return
  const patch = patchFor(record.kind, message.state || {}, false)
  if (message.locked !== undefined) noteLock(message.number, message.locked)
  if (message.reachable !== undefined) patch.bridgedDeviceBasicInformation = { reachable: Boolean(message.reachable) }
  await applyShared(message.number, patch)
}

/* Read the dark modules of the QR code matter.js draws as text: rows of "0" and "1" without the quiet zone. */
function qrRows (payload) {
  const lines = QrCode.encode(payload).split('\n').filter(line => line.length > 0)
  const rows = []
  for (const line of lines) {
    let top = ''
    let bottom = ''
    for (const symbol of line.trim()) {
      // the drawing is light-on-dark: a full block is two light modules, a blank two dark ones
      top += symbol === '█' || symbol === '▀' ? '0' : '1'
      bottom += symbol === '█' || symbol === '▄' ? '0' : '1'
    }
    rows.push(top, bottom)
  }
  const inked = rows.filter(row => row.includes('0'))           // drop the half line above the drawing
  const width = Math.max(...inked.map(row => row.length))
  return inked.map(row => row.padEnd(width, '0')).filter(row => row.includes('1')).map(row => row.slice(1, width - 1))
}

/* Tell the plugin the pairing codes of the bridge and whether a controller has commissioned it. */
function sendPairing () {
  const commissioning = bridge.state.commissioning
  const codes = commissioning.pairingCodes
  let rows = []
  try {
    rows = qrRows(codes.qrPairingCode)
  } catch (error) {
    rows = []
  }
  send({ type: 'pairing', manual: codes.manualPairingCode, qr: codes.qrPairingCode, rows, commissioned: Boolean(commissioning.commissioned) })
}

/* Tell the plugin which controllers (fabrics) the bridge belongs to. */
function sendFabrics () {
  const fabrics = Object.values(bridge.state.commissioning.fabrics || {}).map(fabric => ({
    index: Number(fabric.fabricIndex),
    label: String(fabric.label || '').slice(0, 32),
    vendor: Number(fabric.rootVendorId)
  }))
  send({ type: 'fabrics', fabrics })
}

/* Create the bridge node with its aggregator and the shared devices, then go online. */
async function startBridge (options, entries) {
  bridge = await ServerNode.create({
    id: 'bridge',
    network: { port: options.port },
    // enabled: false – the bridge announces itself for commissioning only when the user opens the window
    commissioning: { passcode: options.passcode, discriminator: options.discriminator, enabled: false },
    productDescription: { name: options.name, deviceType: AggregatorEndpoint.deviceType },
    basicInformation: {
      vendorName: 'ComfyOS',
      vendorId: VendorId(options.vendor_id),
      nodeLabel: options.name,
      productName: 'ComfyOS Matter Bridge',
      productLabel: 'Matter Bridge',
      productId: options.product_id,
      serialNumber: options.serial,
      uniqueId: `bridge-${options.serial}`.slice(0, 32),
      hardwareVersion: 1,
      softwareVersion: 1
    }
  })
  aggregator = new Endpoint(AggregatorEndpoint, { id: 'aggregator', number: AGGREGATOR_NUMBER })
  await bridge.add(aggregator)
  await syncEndpoints(entries)
  bridge.events.commissioning.fabricsChanged.on(() => { sendFabrics(); sendPairing() })
  await bridge.start()
  sendPairing()
  sendFabrics()
}

/* Open or close the commissioning window of the bridge. */
async function setCommissioning (open) {
  if (open) {
    await bridge.act(agent => agent.commissioning.enterCommissionableMode())
  } else {
    await bridge.env.get(DeviceCommissioner).endCommissioning()
  }
  sendPairing()
}

/* Remove one controller from the bridge. */
async function removeFabric (index) {
  const fabric = bridge.env.get(FabricManager).maybeFor(FabricIndex(index))
  if (fabric !== undefined) await fabric.leave()
  sendFabrics()
}

// ---------------------------------------------------------------------------------------------------------
// Controller: devices of other makers
// ---------------------------------------------------------------------------------------------------------

let controller = null                    // the controller ServerNode
const reportTimers = new Map()           // node id -> timer of a pending report
const watched = new Set()                // node ids whose changes are already observed

/* State of one endpoint of a commissioned device, in the same Matter units the bridge uses. */
function remoteState (endpoint) {
  const state = {}
  const read = name => endpoint.maybeStateOf(name)
  const onOff = read('onOff')
  if (onOff) state.on = Boolean(onOff.onOff)
  const level = read('levelControl')
  if (level && level.currentLevel !== undefined) state.level = level.currentLevel
  const color = read('colorControl')
  if (color) {
    if (color.colorTemperatureMireds !== undefined) state.mireds = color.colorTemperatureMireds
    if (color.currentX !== undefined) state.x = color.currentX
    if (color.currentY !== undefined) state.y = color.currentY
    if (color.colorTempPhysicalMinMireds !== undefined) state.min_mireds = color.colorTempPhysicalMinMireds
    if (color.colorTempPhysicalMaxMireds !== undefined) state.max_mireds = color.colorTempPhysicalMaxMireds
    if (color.colorMode === COLOR_MODE_XY) state.color_mode = 'xy'
    if (color.colorMode === COLOR_MODE_TEMPERATURE) state.color_mode = 'ct'
  }
  const thermostat = read('thermostat')
  if (thermostat) {
    if (thermostat.localTemperature !== undefined) state.local_temperature = thermostat.localTemperature
    if (thermostat.occupiedHeatingSetpoint !== undefined) state.heating_setpoint = thermostat.occupiedHeatingSetpoint
    if (thermostat.absMinHeatSetpointLimit !== undefined) state.min_heat = thermostat.absMinHeatSetpointLimit
    if (thermostat.absMaxHeatSetpointLimit !== undefined) state.max_heat = thermostat.absMaxHeatSetpointLimit
  }
  const covering = read('windowCovering')
  if (covering && covering.currentPositionLiftPercent100ths !== undefined) state.position = covering.currentPositionLiftPercent100ths
  const contact = read('booleanState')
  if (contact) state.contact = Boolean(contact.stateValue)
  const occupancy = read('occupancySensing')
  if (occupancy && occupancy.occupancy) state.occupied = Boolean(occupancy.occupancy.occupied)
  const temperature = read('temperatureMeasurement')
  if (temperature && temperature.measuredValue !== undefined) state.temperature = temperature.measuredValue
  const humidity = read('relativeHumidityMeasurement')
  if (humidity && humidity.measuredValue !== undefined) state.humidity = humidity.measuredValue
  const alarm = read('smokeCoAlarm')
  if (alarm && alarm.smokeState !== undefined) state.smoke = alarm.smokeState !== SMOKE_NORMAL
  return state
}

/* All endpoints of a commissioned device, the root endpoint first. */
function endpointsOf (node) {
  const found = []
  const walk = endpoint => {
    found.push(endpoint)
    for (const part of endpoint.parts) walk(part)
  }
  walk(node)
  return found
}

/* Describe a commissioned device for the plugin: identity, reachability and every endpoint with its types. */
function describeNode (node) {
  const basic = node.maybeStateOf('basicInformation') || {}
  const endpoints = []
  for (const endpoint of endpointsOf(node)) {
    const descriptor = endpoint.maybeStateOf('descriptor')
    if (!descriptor || endpoint.number === undefined) continue
    const bridged = endpoint.maybeStateOf('bridgedDeviceBasicInformation') || {}
    endpoints.push({
      endpoint: Number(endpoint.number),
      types: (descriptor.deviceTypeList || []).map(entry => Number(entry.deviceType)).slice(0, 8),
      name: String(bridged.nodeLabel || bridged.productName || '').slice(0, 64),
      reachable: bridged.reachable === undefined ? true : Boolean(bridged.reachable),
      state: remoteState(endpoint)
    })
  }
  return {
    type: 'node',
    node: String(node.id),
    online: Boolean(node.lifecycle.isConnected),
    name: String(basic.nodeLabel || basic.productName || '').slice(0, 64),
    vendor: String(basic.vendorName || '').slice(0, 64),
    product: String(basic.productName || '').slice(0, 64),
    serial: String(basic.serialNumber || '').slice(0, 64),
    endpoints: endpoints.slice(0, 200)
  }
}

const observed = new WeakSet()          // endpoints of commissioned devices whose attribute changes are observed

/* Observe the attributes of every endpoint of a device that is not observed yet. The endpoints of a freshly
   commissioned device appear a moment after commissioning, so this runs again before every report. */
function observeEndpoints (node) {
  for (const endpoint of endpointsOf(node)) {
    if (observed.has(endpoint)) continue
    observed.add(endpoint)
    for (const name of CLIENT_CLUSTERS) {
      let events
      try {
        events = endpoint.behaviors.has(name) ? endpoint.eventsOf(name) : undefined
      } catch (error) {
        events = undefined               // the endpoint does not have this cluster
      }
      if (!events) continue
      for (const attribute of CLIENT_ATTRIBUTES[name]) {
        const event = events[`${attribute}$Changed`]
        if (event && typeof event.on === 'function') event.on(() => reportNodeSoon(node))
      }
    }
  }
}

/* Send a fresh description of a device shortly after something about it changed. */
function reportNodeSoon (node) {
  const id = String(node.id)
  if (reportTimers.has(id)) return
  reportTimers.set(id, setTimeout(() => {
    reportTimers.delete(id)
    try {
      if (controller.peers.get(id) !== node) return
      observeEndpoints(node)
      send(describeNode(node))
    } catch (error) {
      sendError('node_report_failed', error)
    }
  }, NODE_REPORT_MS))
}

/* Observe a commissioned device: an attribute change, a changed structure and its connection state lead to a
   new report. */
function watchNode (node) {
  const id = String(node.id)
  if (watched.has(id)) return
  watched.add(id)
  node.lifecycle.connectionStateChanged.on(() => reportNodeSoon(node))
  node.lifecycle.changed.on(() => reportNodeSoon(node))
  reportNodeSoon(node)
}

/* Attributes of a commissioned device whose changes are reported, by cluster. */
const CLIENT_ATTRIBUTES = {
  onOff: ['onOff'],
  levelControl: ['currentLevel'],
  colorControl: ['colorTemperatureMireds', 'currentX', 'currentY', 'colorMode'],
  thermostat: ['localTemperature', 'occupiedHeatingSetpoint'],
  windowCovering: ['currentPositionLiftPercent100ths'],
  booleanState: ['stateValue'],
  occupancySensing: ['occupancy'],
  temperatureMeasurement: ['measuredValue'],
  relativeHumidityMeasurement: ['measuredValue'],
  smokeCoAlarm: ['smokeState'],
  bridgedDeviceBasicInformation: ['reachable', 'nodeLabel'],
  basicInformation: ['nodeLabel']
}
const CLIENT_CLUSTERS = Object.keys(CLIENT_ATTRIBUTES)

/* Create the controller node with its own fabric and report the devices it already knows. */
async function startController (options) {
  controller = await ServerNode.create(ServerNode.RootEndpoint.with(ControllerBehavior), {
    id: 'controller',
    network: { port: options.port },
    commissioning: { enabled: false },
    controller: { adminFabricLabel: String(options.label || 'ComfyOS').slice(0, 32), ble: false },
    basicInformation: {
      vendorName: 'ComfyOS',
      vendorId: VendorId(options.vendor_id),
      nodeLabel: 'ComfyOS',
      productName: 'ComfyOS Matter Controller',
      productLabel: 'Matter Controller',
      productId: options.product_id,
      serialNumber: options.serial,
      uniqueId: `controller-${options.serial}`.slice(0, 32)
    }
  })
  await controller.start()
  // Known devices are reported before `started`, so the plugin has the complete list when it goes online.
  for (const node of controller.peers.commissioned) {
    watchNode(node)
    try {
      observeEndpoints(node)
      send(describeNode(node))
    } catch (error) {
      sendError('node_report_failed', error)
    }
  }
}

/* Commission a device with the code another controller's "share" function shows. Attestation that cannot be
   verified offline is accepted and reported, never looked up on the internet. */
async function pair (message) {
  const findings = []
  try {
    const node = await controller.peers.commission({
      pairingCode: String(message.code),
      onAttestationFailure: found => {
        for (const finding of found || []) {
          const level = finding && typeof finding.level === 'string' ? finding.level : 'info'
          const check = finding && finding.type !== undefined ? String(finding.type) : 'unknown'
          findings.push(`${level}:${check}`.replace(/[^A-Za-z0-9:_-]/g, '').slice(0, 60))
        }
        return true
      }
    })
    watchNode(node)
    send({ type: 'result', id: message.id, ok: true, node: String(node.id), attestation: findings.slice(0, 10) })
  } catch (error) {
    if (debugLog) process.stderr.write(`pair: ${error && error.stack}\n`)
    send({ type: 'result', id: message.id, ok: false, error: errorCode(error) })
  }
}

/* Leave the fabric of a device and forget it. A device that no longer answers is forgotten anyway. */
async function unpair (message) {
  const node = controller.peers.get(String(message.node))
  if (node === undefined) return send({ type: 'result', id: message.id, ok: false, error: 'unknown_node' })
  let clean = true
  try {
    await node.decommission()
  } catch (error) {
    clean = false
    await node.delete()
  }
  watched.delete(String(message.node))
  send({ type: 'node_removed', node: String(message.node) })
  send({ type: 'result', id: message.id, ok: true, clean })
}

/* The endpoint with this number on a commissioned device. */
function remoteEndpoint (node, number) {
  return endpointsOf(node).find(endpoint => Number(endpoint.number) === Number(number))
}

/* Switch a commissioned device: translate the wanted values into the commands of its clusters. */
async function invoke (message) {
  try {
    const node = controller.peers.get(String(message.node))
    const endpoint = node && remoteEndpoint(node, message.endpoint)
    if (!endpoint) return send({ type: 'result', id: message.id, ok: false, error: 'unknown_endpoint' })
    const state = message.state || {}
    const options = { optionsMask: {}, optionsOverride: {} }
    if (state.level !== undefined) {
      await endpoint.commandsOf('levelControl').moveToLevelWithOnOff({ level: state.level, transitionTime: 0, ...options })
    } else if (state.on !== undefined) {
      await (state.on ? endpoint.commandsOf('onOff').on() : endpoint.commandsOf('onOff').off())
    }
    if (state.mireds !== undefined) {
      await endpoint.commandsOf('colorControl').moveToColorTemperature({ colorTemperatureMireds: state.mireds, transitionTime: 0, ...options })
    }
    if (state.x !== undefined && state.y !== undefined) {
      await endpoint.commandsOf('colorControl').moveToColor({ colorX: state.x, colorY: state.y, transitionTime: 0, ...options })
    }
    if (state.heating_setpoint !== undefined) {
      await endpoint.set({ thermostat: { occupiedHeatingSetpoint: state.heating_setpoint } })
    }
    if (state.position !== undefined) {
      await endpoint.commandsOf('windowCovering').goToLiftPercentage({ liftPercent100thsValue: state.position })
    } else if (state.cover === 'up') {
      await endpoint.commandsOf('windowCovering').upOrOpen()
    } else if (state.cover === 'down') {
      await endpoint.commandsOf('windowCovering').downOrClose()
    } else if (state.cover === 'stop') {
      await endpoint.commandsOf('windowCovering').stopMotion()
    }
    send({ type: 'result', id: message.id, ok: true })
    reportNodeSoon(node)
  } catch (error) {
    if (debugLog) process.stderr.write(`invoke: ${error && error.stack}\n`)
    send({ type: 'result', id: message.id, ok: false, error: errorCode(error) })
  }
}

// ---------------------------------------------------------------------------------------------------------
// Message loop
// ---------------------------------------------------------------------------------------------------------

let started = false
let queue = Promise.resolve()            // messages that change nodes run one after the other

/* Start both nodes from the plugin's `start` message. */
async function start (message) {
  if (started) return
  started = true
  await startBridge(message.bridge, message.endpoints || [])
  if (message.controller && message.controller.enabled !== false) await startController(message.controller)
  send({ type: 'started' })
}

/* Handle one message of the plugin. Replies and storage are handled at once; everything that touches the
   nodes is queued so two changes never overlap. */
function handle (message) {
  if (message === null || typeof message !== 'object') return
  if (message.type === 'reply') {
    const resolve = waiting.get(message.id)
    if (resolve) {
      waiting.delete(message.id)
      resolve(String(message.status))
    }
    return
  }
  if (message.type === 'storage' && !started) return loadStorage(message)
  if (message.type === 'pair' || message.type === 'invoke' || message.type === 'unpair') {
    if (!controller) return send({ type: 'result', id: message.id, ok: false, error: 'no_controller' })
    const run = message.type === 'pair' ? pair : message.type === 'invoke' ? invoke : unpair
    run(message).catch(error => sendError(`${message.type}_failed`, error))
    return
  }
  const task = {
    start: () => start(message),
    endpoints: () => bridge && syncEndpoints(message.endpoints || []),
    update: () => bridge && updateShared(message),
    commissioning: () => bridge && setCommissioning(Boolean(message.open)),
    remove_fabric: () => bridge && removeFabric(message.index)
  }[message.type]
  if (task === undefined) return
  queue = queue.then(task).catch(error => sendError(`${message.type}_failed`, error))
}

/* Close both nodes, send what storage still holds back, and exit. */
async function shutdown () {
  setTimeout(() => process.exit(0), SHUTDOWN_LIMIT_MS).unref()     // never outlive ComfyOS, even if closing hangs
  try {
    if (controller) await controller.close()
    if (bridge) await bridge.close()
  } catch (error) {
    sendError('shutdown_failed', error)
  }
  if (flushTimer !== null) clearTimeout(flushTimer)
  flushStorage()
  process.stdout.write('', () => process.exit(0))
}

process.on('uncaughtException', error => { sendError('uncaught_' + errorCode(error), error); process.exit(1) })
process.on('unhandledRejection', error => { sendError('unhandled_' + errorCode(error), error) })

const input = createInterface({ input: process.stdin, crlfDelay: Infinity })
input.on('line', line => {
  if (line.length > MAX_LINE) return sendError('line_too_long')
  let message
  try {
    message = JSON.parse(line)
  } catch (error) {
    return sendError('bad_json')
  }
  try {
    handle(message)
  } catch (error) {
    sendError('handler_failed', error)
  }
})
input.on('close', shutdown)             // stdin closed: ComfyOS is gone or stops the helper

send({ type: 'ready', protocol: PROTOCOL })
