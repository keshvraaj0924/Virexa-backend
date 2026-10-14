/**
 * Run tests with their native framework, failing closed on ambiguous files.
 * Node's test runner cannot execute Vitest suites, and Vitest cannot safely
 * collect node:test suites. Classification uses each file's explicit import.
 */
import { spawnSync } from 'node:child_process'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const rootDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const requestedRunner = process.argv[2]

if (requestedRunner !== 'vitest' && requestedRunner !== 'node') {
  console.error('Usage: node scripts/run-tests.mjs <vitest|node>')
  process.exit(2)
}

async function discoverTests(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const results = []
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) {
      results.push(...await discoverTests(path))
    } else if (entry.isFile() && entry.name.endsWith('.test.ts')) {
      results.push(path)
    }
  }
  return results
}

const allTests = [
  ...await discoverTests(resolve(rootDirectory, 'src')),
  ...await discoverTests(resolve(rootDirectory, 'tests')),
]
const selectedTests = []

for (const testFile of allTests) {
  const source = await readFile(testFile, 'utf8')
  const usesVitest = /\bfrom\s*['"]vitest['"]|\bimport\s*['"]vitest['"]/.test(source)
  const usesNodeTest = /\bfrom\s*['"]node:test['"]|\bimport\s*['"]node:test['"]/.test(source)
  if (usesVitest === usesNodeTest) {
    throw new Error(`Cannot uniquely classify ${relative(rootDirectory, testFile)} as Vitest or node:test`)
  }
  if ((requestedRunner === 'vitest') === usesVitest) {
    selectedTests.push(relative(rootDirectory, testFile))
  }
}

if (selectedTests.length === 0) {
  throw new Error(`No ${requestedRunner} test suites found`)
}

const argumentsForRunner = requestedRunner === 'vitest'
  ? [resolve(rootDirectory, 'node_modules/vitest/vitest.mjs'), 'run', ...selectedTests]
  : ['--import=tsx', '--test', '--test-concurrency=1', ...selectedTests]

console.log(`Executing ${selectedTests.length} ${requestedRunner} test files`)
const result = spawnSync(process.execPath, argumentsForRunner, {
  cwd: rootDirectory,
  stdio: 'inherit',
  env: process.env,
})
if (result.error) throw result.error
process.exit(result.status ?? 1)
