import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { resolve } from 'node:path'

const testFiles = readdirSync(resolve('tests'))
  .filter((file) => file.endsWith('.test.ts'))
  .sort()

if (testFiles.length === 0) {
  console.error('No Node integration test files were found.')
  process.exit(1)
}

for (const file of testFiles) {
  console.log(`\n=== Running ${file} ===`)
  const result = spawnSync(
    process.execPath,
    ['--import', 'tsx', '--test', '--test-concurrency=1', resolve('tests', file)],
    { stdio: 'inherit' },
  )

  if (result.error) {
    console.error(`Failed to start integration test ${file}:`, result.error)
    process.exit(1)
  }

  if (result.status !== 0) {
    console.error(`Integration test failed: ${file}`)
    process.exit(result.status ?? 1)
  }
}

console.log(`\nAll ${testFiles.length} Node integration test files passed.`)
