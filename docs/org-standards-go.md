<!-- junglestyle/.github code-standards/go.md at 02947e75f593e165c491e947898134f05cca5421. Change it there, then run make sync-standards. -->
# Go standards

Decisions for Go code, beneath the org [standards](https://github.com/junglestyle/.github/blob/main/code-standards/README.md). `.golangci.yml` holds the mechanical rules; this file holds what a linter can't check. No junglestyle repo uses Go yet, so every rule here comes from the sources below and the org standards, not from practice.

## Errors

- Handle an error once: return it or log it, never both.
- Add context only when it says what this function was doing: `fmt.Errorf("open config %s: %w", path, err)`. No "failed to" or "error"; they pile up as the error climbs.
- Wrap with `%w` when a caller may match the cause. Use `%v` where an error leaves the program (an HTTP response, another service) and the cause is an internal detail; the linter wants a `//nolint:errorlint` saying so.
- Pick the error's form by what the caller needs. Nothing to match: `errors.New` or `fmt.Errorf`. A fixed condition to match: an `ErrX` variable. Fields to read: an `XError` type. Exported ones are API.
- Match with `errors.Is` and `errors.As`, never by comparing message text.
- Return `error`, never a concrete error pointer type.
- A missing result is `(v, ok bool)` or `(v, error)`, never an in-band `-1`, `""` or `nil`.
- No `panic` for an ordinary error. `MustX` helpers are only for package-level values built from constants.
- `main` is `if err := run(); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }`. Nothing else calls `os.Exit` or `log.Fatal`.
- An error discarded with `_` gets a comment saying why that's safe.

## Layout

- One module per repo. `go.mod`'s `go` line names the current release.
- One binary: `package main` at the root. Several: `cmd/<name>/main.go` each.
- Packages that aren't a public API live under `internal/`. No `pkg/` directory.
- Name a package for what it provides (`queue`, `lease`), never `util`, `common`, `types`, `models` or `helpers`.
- An interface belongs to the package that consumes it. Return concrete types. Don't declare an interface until a second implementation or a test fake needs one.
- No `init()` with side effects and no mutable package-level state. Build dependencies in `run` and pass them down.
- Configure with flags and env vars read in `main`, through the stdlib `flag` package. Constructors take a config struct, not functional options.

## Concurrency

- Write synchronous functions. A caller that wants concurrency adds its own goroutine.
- Every goroutine has a way to be told to stop, and someone waits for it: a `sync.WaitGroup`, a `done` channel, or `errgroup`.
- `ctx context.Context` is the first parameter of anything that does I/O or can block. Never store it in a struct. `context.Background()` appears only in `main` and tests.
- Channels are unbuffered or of size one. Any other size has a comment saying what bounds it.
- A mutex sits beside the fields it guards, as a named field, not embedded. Its zero value is ready to use.
- No goroutines started from `init` or a constructor. The caller starts them and owns their lifetime.
- `go test -race` runs in `make check`.

## Testing

- Use the stdlib `testing` package. No testify, gomega or other assertion library, and no gomock or mockery.
- Fake a dependency with a small struct in the test file. Prefer real ones where they're cheap: `httptest`, `t.TempDir()`, a scratch database.
- Failures name the call, the input, what it got, then what it wanted: `t.Errorf("Parse(%q) = %v, want %v", in, got, want)`.
- `t.Errorf` to keep checking; `t.Fatalf` only when the test can't go on. Helpers call `t.Helper()`.
- Use a table when cases share one shape. Name subtests so `go test -run` can select them.
- Compare structs with `go-cmp`'s `cmp.Diff` and print the diff.
- Test errors by meaning (`errors.Is`, or just `err != nil`), never by message text.
- Tests run in parallel only if they were written to.

## Dependencies

- The stdlib first: `net/http`, `log/slog`, `flag`, `database/sql`, `encoding/json`. `golang.org/x/...` counts as nearly stdlib.
- For Postgres use pgx; for SQLite, `modernc.org/sqlite`, which needs no cgo.
- No web frameworks, DI containers, ORMs, viper or cobra.
- Commit `go.sum`. `make check` fails if `go mod tidy` would change anything. Don't vendor.

## Proverbs

From Rob Pike's [Go Proverbs](https://go-proverbs.github.io/), the ones these rules lean on:

- Don't communicate by sharing memory, share memory by communicating.
- The bigger the interface, the weaker the abstraction.
- Make the zero value useful.
- A little copying is better than a little dependency.
- Clear is better than clever.
- Errors are values.
- Don't just check errors, handle them gracefully.
- Don't panic.

---

Drawn from [Go Code Review Comments](https://go.dev/wiki/CodeReviewComments) (the Go Authors), [Go Proverbs](https://go-proverbs.github.io/) (Rob Pike), the [Google Go Style Guide](https://google.github.io/styleguide/go/decisions) (CC BY 3.0) and the [Uber Go Style Guide](https://github.com/uber-go/guide/blob/master/style.md) (Apache 2.0), adapted.
