import { readdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'

const testFiles = readdirSync('tests')
  .filter((file) => file.endsWith('.test.ts'))
  .sort()

if (testFiles.length === 0) {
  console.error('No Node integration test files were found.')
  process.exit(1)
}

for (const file of testFiles) {
  console.log(`\n=== Running ${file} ===`)

  try {
    execFileSync(
      process.execPath,
      ['--import=tsx', '--test', '--test-concurrency=1', `tests/${file}`],
      { stdio: 'inherit' },
    )
  } catch (error) {
    const exitCode = typeof error === 'object' && error !== null && 'status' in error
      ? Number(error.status)
      : 1

    console.error(`Integration test failed: ${file}`)
    process.exit(Number.isInteger(exitCode) && exitCode > 0 ? exitCode : 1)
  }
}

console.log(`\nAll ${testFiles.length} Node integration test files passed.`)
