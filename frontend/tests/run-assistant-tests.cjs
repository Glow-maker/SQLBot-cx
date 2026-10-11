const { build } = require('esbuild')
const { spawnSync } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const { parse, compileScript } = require('@vue/compiler-sfc')
const out = path.resolve(__dirname, '../node_modules/.cache/assistant-tests.cjs')
build({
  entryPoints: [path.join(__dirname, 'assistant.test.ts')],
  outfile: out,
  bundle: true,
  platform: 'node',
  format: 'cjs',
  external: ['vue'],
  plugins: [
    {
      name: 'answer-fixtures',
      setup(build) {
        build.onResolve({ filter: /^@\/api\/chat\.ts$/ }, () => ({
          path: path.join(__dirname, 'chat-fixture.ts'),
        }))
        build.onResolve({ filter: /^@\// }, (args) => ({
          path:
            path.resolve(__dirname, '../src', args.path.slice(2)) +
            (path.extname(args.path) ? '' : '.ts'),
        }))
        build.onLoad({ filter: /\.vue$/ }, (args) => {
          if (
            !/answer[\\/](Chart|Analysis|Predict)Answer\.vue$/.test(args.path) &&
            !/Assistant(Avatar|Companion)\.vue$/.test(args.path)
          )
            return { contents: 'export default {}', loader: 'js' }
          const { descriptor } = parse(fs.readFileSync(args.path, 'utf8'))
          return {
            contents: compileScript(descriptor, { id: 'test-answer' }).content,
            loader: 'ts',
            resolveDir: path.dirname(args.path),
          }
        })
      },
    },
  ],
})
  .then(() => {
    const result = spawnSync(process.execPath, ['--test', out], { stdio: 'inherit' })
    process.exit(result.status ?? 1)
  })
  .catch((error) => {
    console.error(error)
    process.exit(1)
  })
