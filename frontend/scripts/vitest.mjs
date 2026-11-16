// Runs Vitest with a normalised working directory (invoked via `npm test`).
//
// On Windows, some terminals (e.g. VS Code) report the working directory with a
// lower-case drive letter ("c:\..."). Vitest then loads its runner twice under
// differently-cased paths and every suite fails with "failed to find the
// current suite". Upper-casing the drive letter avoids this. On other
// platforms the working directory is passed through unchanged.
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'

const cwd = process.cwd().replace(/^[a-z]:/, (drive) => drive.toUpperCase())
const vitestCli = join(cwd, 'node_modules', 'vitest', 'vitest.mjs')

const result = spawnSync(process.execPath, [vitestCli, ...process.argv.slice(2)], { cwd, stdio: 'inherit' })
process.exit(result.status ?? 1)
