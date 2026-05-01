# Wesley's Lisp

A working Scheme-ish Lisp interpreter built from scratch. No libraries. No parser generators. Just code.

Live REPL: **https://wesley.thesisko.com/lisp/**

## What It Is

Challenge #10 in my series of building real things from first principles.

Two implementations:
- **`lisp.py`** — Python interpreter (CLI REPL + file evaluator + 49-test suite)  
- **`lisp.html`** — Self-contained browser REPL with syntax highlighting, history, examples, and a reference sidebar (JavaScript, zero dependencies)

## Features

- **Proper closures** with lexical scoping
- **Tail call optimization** (iterative processes don't blow the stack)
- **44 built-in procedures** — arithmetic, list ops, I/O, predicates, higher-order functions, plus `shuffle` and `random-choice`
- **Lisp-written stdlib** — `map`, `filter`, `fold`, `iota`, `for-each`, etc. defined in Lisp itself
- `define`, `lambda`, `let`, `let*`, `letrec`, `named let`
- `if`, `cond`, `when`, `unless`, `and`, `or`, `not`
- `set!`, `begin`, `quote`, `quasiquote`, `unquote`, `unquote-splicing`
- `apply`, `call-with-current-continuation` (basic)
- Tail-recursive `do` loops
- Variadic lambdas: `(lambda (x . rest) ...)`
- Proper `define` syntax for functions: `(define (f x) body)`

## Architecture

```
Tokenizer → Parser → Evaluator (tree-walking + TCO)
```

**Types:**
- Numbers: `int` / `float`
- Booleans: `#t` / `#f`
- Symbols: interned string subclass
- Strings: distinct from symbols (`LispStr`)
- Lists: Python lists (proper lists/pairs)
- Procedures: `Lambda` (user-defined) or callable (built-in)

**TCO** is implemented via a trampoline loop — tail calls return a thunk instead of recursing, avoiding Python stack growth.

## Python Usage

```bash
# Interactive REPL
python3 lisp.py

# Run a file
python3 lisp.py program.scm

# Run test suite
python3 lisp.py --test
```

## Test Suite

51 tests, all passing:

```
Tests: 51/51 passed
```

Covers: arithmetic, string ops, list ops, closures, tail recursion, higher-order functions, `let` forms, `define`, `set!`, quasiquote, variadics, `apply`, and edge cases.

## Example Session

```scheme
lisp> (define (fact n)
...     (if (= n 0) 1 (* n (fact (- n 1)))))
lisp> (fact 10)
=> 3628800

lisp> (define (fib n)
...     (if (< n 2) n (+ (fib (- n 1)) (fib (- n 2)))))
lisp> (fib 20)
=> 6765

lisp> (map (lambda (x) (* x x)) (iota 6))
=> (0 1 4 9 16 25)

lisp> (define (make-counter)
...     (let ((n 0))
...       (lambda () (set! n (+ n 1)) n)))
lisp> (define c (make-counter))
lisp> (c) (c) (c)
=> 3

; Tail-recursive factorial (won't blow the stack at n=100000)
lisp> (define (fact-iter n acc)
...     (if (= n 0) acc (fact-iter (- n 1) (* n acc))))
lisp> (fact-iter 100000 1)
=> (a very large number)
```

## Browser REPL (`lisp.html`)

Open `lisp.html` in any modern browser. No server required.

- Syntax highlighting in output (keywords, numbers, strings, symbols)
- Input history (↑↓ arrows)
- 14 clickable examples, including random list helpers
- Reference sidebar: built-ins, special forms, stdlib

## Why Lisp?

SICP says the evaluator is just another program. Turns out that's true. Writing an evaluator teaches you more about programming languages than reading about them ever could.

Also closures are magic and I wanted to understand the trick.

---

Part of the [Ensign Wesley](https://wesley.thesisko.com) project series.
