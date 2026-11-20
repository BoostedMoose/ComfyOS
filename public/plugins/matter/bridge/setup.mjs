/* Runs before matter.js loads (bridge.mjs imports this file first).

   matter.js configures itself for Node.js while it is imported: it would read a config file, parse the command
   line, take variables from the process environment, open a storage folder below HOME and install signal
   handlers. The helper wants none of that – it gets everything from the plugin over stdin and must not touch
   the file system. The switches below are only accepted before matter.js has initialised. */
import fs from 'node:fs'
import { syncBuiltinESMExports } from 'node:module'
import { config } from '@matter/nodejs/config'

/* matter.js asks whether a config file exists even when told not to load one. Under `node --permission` that
   question is refused with an exception, which would end the helper before it starts. A refused question is
   answered with "no" here; reading stays forbidden. */
const existsSync = fs.existsSync
fs.existsSync = path => {
  try {
    return existsSync(path)
  } catch (error) {
    return false
  }
}
syncBuiltinESMExports()

config.loadConfigFile = false        // no config.json
config.loadProcessArgv = false       // the command line carries only the path of this script
config.loadProcessEnv = false        // nothing from the environment changes how matter.js behaves
config.installFilesystem = false     // no file system service: storage namespaces stay in memory
config.initializeStorage = false     // no file, WAL or SQLite storage drivers – bridge.mjs registers its own
config.trapProcessSignals = false    // the plugin ends the helper by closing stdin
config.trapUnhandledErrors = false   // bridge.mjs reports them as an error code and exits
config.setProcessExitCodeOnError = false
