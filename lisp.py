#!/usr/bin/env python3
"""
lisp.py — A minimal Lisp interpreter in Python from scratch.

Architecture:
  tokenize(src)     → [Token]          lexer
  parse(tokens)     → AST              recursive descent parser
  evaluate(expr, env) → value          tree-walking evaluator with TCO

Types:
  int / float       → Lisp numbers
  bool              → #t / #f  (NOTE: bool is subclass of int in Python)
  Symbol            → str subclass, interned
  LispStr           → strings  (distinct from symbols)
  list              → proper list / pair
  Lambda            → user-defined function with lexical closure
  callable          → built-in procedure

Usage:
  python3 lisp.py                 -- interactive REPL
  python3 lisp.py --test          -- run test suite
  python3 lisp.py file.scm        -- evaluate a file
"""

import math
import operator
import random
import sys
from typing import Any, List, Optional

# ─────────────────────────────────────────────────────────────
#  TYPES
# ─────────────────────────────────────────────────────────────

class LispError(Exception):
    """Lisp evaluation / syntax error with optional position info."""
    pass


class Symbol(str):
    """A Lisp symbol.  Subclasses str so we can use it as a dict key."""
    __slots__ = ()
    _cache: dict = {}

    def __new__(cls, s: str):
        if s in cls._cache:
            return cls._cache[s]
        obj = super().__new__(cls, s)
        cls._cache[s] = obj
        return obj

    def __repr__(self):
        return str(self)


class LispStr:
    """A Lisp string — distinct from a Symbol."""
    __slots__ = ('s',)

    def __init__(self, s: str):
        self.s = s

    def __repr__(self):
        escaped = self.s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t')
        return f'"{escaped}"'

    def __eq__(self, other):
        return isinstance(other, LispStr) and self.s == other.s

    def __hash__(self):
        return hash(('LispStr', self.s))


class Lambda:
    """A user-defined function with lexical closure (Scheme-style)."""
    __slots__ = ('params', 'body', 'env', 'name', 'variadic')

    def __init__(self, params, body, env, name=None, variadic=False):
        self.params   = params    # list of Symbol (param names)
        self.body     = body      # list of expressions (implicit begin)
        self.env      = env       # enclosing environment
        self.name     = name      # set by define for error messages
        self.variadic = variadic  # True if last param is rest arg

    def __repr__(self):
        return f'#<lambda {self.name or ""}>'


class Env:
    """
    An environment: a dict of name→value bindings plus an outer scope.
    Implements the standard Scheme environment model.
    """
    __slots__ = ('d', 'outer')

    def __init__(self, params=(), args=(), outer=None):
        self.d:     dict          = {}
        self.outer: Optional[Env] = outer
        if params:
            # Handle variadic args: (define (f x . rest) ...)
            if isinstance(params, Symbol):          # (lambda args body)
                self.d[str(params)] = list(args)
            else:
                for i, p in enumerate(params):
                    if str(p) == '.':               # dotted pair rest args
                        self.d[str(params[i+1])] = list(args[i:])
                        break
                    self.d[str(p)] = args[i] if i < len(args) else []

    def lookup(self, name: str) -> Any:
        if name in self.d:
            return self.d[name]
        if self.outer is not None:
            return self.outer.lookup(name)
        raise LispError(f"Undefined symbol: '{name}'")

    def define(self, name: str, val: Any) -> None:
        self.d[name] = val

    def set(self, name: str, val: Any) -> None:
        """Mutate an existing binding (for set!)."""
        if name in self.d:
            self.d[name] = val
            return
        if self.outer is not None:
            self.outer.set(name, val)
            return
        raise LispError(f"set!: unbound variable '{name}'")


# ─────────────────────────────────────────────────────────────
#  TOKENIZER
# ─────────────────────────────────────────────────────────────

class Token:
    __slots__ = ('kind', 'val', 'line', 'col')

    # kind ∈ 'LPAREN' 'RPAREN' 'QUOTE' 'QUASI' 'UNQUOTE' 'SPLICE'
    #        'NUM' 'STR' 'SYM'
    def __init__(self, kind: str, val: str, line: int, col: int):
        self.kind = kind
        self.val  = val
        self.line = line
        self.col  = col

    def __repr__(self):
        return f'{self.kind}({self.val!r})@{self.line}:{self.col}'


def tokenize(src: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0; line = 1; col = 1; n = len(src)

    while i < n:
        c = src[i]
        tl, tc = line, col

        # ── Whitespace ──────────────────────────────────
        if c in ' \t\r\n':
            if c == '\n': line += 1; col = 1
            else:         col  += 1
            i += 1; continue

        # ── Comment ─────────────────────────────────────
        if c == ';':
            while i < n and src[i] != '\n':
                i += 1; col += 1
            continue

        # ── Single-char tokens ───────────────────────────
        if c == '(':
            tokens.append(Token('LPAREN', '(', tl, tc)); i += 1; col += 1; continue
        if c == ')':
            tokens.append(Token('RPAREN', ')', tl, tc)); i += 1; col += 1; continue

        # ── Quote shorthands ────────────────────────────
        if c == "'":
            tokens.append(Token('QUOTE',   "'",  tl, tc)); i += 1; col += 1; continue
        if c == '`':
            tokens.append(Token('QUASI',   '`',  tl, tc)); i += 1; col += 1; continue
        if c == ',':
            if i+1 < n and src[i+1] == '@':
                tokens.append(Token('SPLICE', ',@', tl, tc)); i += 2; col += 2
            else:
                tokens.append(Token('UNQUOTE', ',', tl, tc)); i += 1; col += 1
            continue

        # ── String literal ───────────────────────────────
        if c == '"':
            i += 1; col += 1
            buf = ''
            while i < n and src[i] != '"':
                ch = src[i]; i += 1; col += 1
                if ch == '\n': line += 1; col = 1
                if ch == '\\' and i < n:
                    esc = src[i]; i += 1; col += 1
                    buf += {'n':'\n','t':'\t','r':'\r','\\':'\\',
                            '"':'"','0':'\0'}.get(esc, esc)
                else:
                    buf += ch
            if i >= n:
                raise LispError(f'Unterminated string at {tl}:{tc}')
            i += 1; col += 1   # closing "
            tokens.append(Token('STR', buf, tl, tc))
            continue

        # ── Atom: number or symbol ───────────────────────
        buf = ''
        while i < n and src[i] not in ' \t\r\n();\'"`,':
            buf += src[i]; i += 1; col += 1

        if not buf:
            raise LispError(f'Unexpected character {c!r} at {tl}:{tc}')

        # Detect numeric token (integers and floats, including #e/#i prefixes)
        is_num = False
        try:
            float(buf); is_num = True
        except ValueError:
            pass

        tokens.append(Token('NUM' if is_num else 'SYM', buf, tl, tc))

    return tokens


# ─────────────────────────────────────────────────────────────
#  PARSER  (recursive descent)
# ─────────────────────────────────────────────────────────────

NIL = []   # empty list / nil sentinel

def parse(src: str) -> List[Any]:
    """Tokenize + parse all top-level expressions.  Returns a list of ASTs."""
    toks = tokenize(src)
    pos  = [0]
    out  = []
    while pos[0] < len(toks):
        out.append(_expr(toks, pos))
    return out


def _expr(toks: List[Token], pos: List[int]) -> Any:
    if pos[0] >= len(toks):
        raise LispError('Unexpected end of input')

    t = toks[pos[0]]; pos[0] += 1

    # ── Atoms ────────────────────────────────────────────
    if t.kind == 'NUM':
        v = t.val
        return int(v) if '.' not in v and 'e' not in v.lower() else float(v)

    if t.kind == 'STR':
        return LispStr(t.val)

    if t.kind == 'SYM':
        s = t.val
        if s in ('#t', '#true'):  return True
        if s in ('#f', '#false'): return False
        if s in ('nil', '()'):    return NIL
        return Symbol(s)

    # ── Quote shorthands ─────────────────────────────────
    if t.kind == 'QUOTE':
        return [Symbol('quote'),            _expr(toks, pos)]
    if t.kind == 'QUASI':
        return [Symbol('quasiquote'),       _expr(toks, pos)]
    if t.kind == 'UNQUOTE':
        return [Symbol('unquote'),          _expr(toks, pos)]
    if t.kind == 'SPLICE':
        return [Symbol('unquote-splicing'), _expr(toks, pos)]

    # ── List ─────────────────────────────────────────────
    if t.kind == 'LPAREN':
        items = []
        while pos[0] < len(toks) and toks[pos[0]].kind != 'RPAREN':
            items.append(_expr(toks, pos))
        if pos[0] >= len(toks):
            raise LispError(f'Missing ) to match ( at {t.line}:{t.col}')
        pos[0] += 1   # consume ')'
        return items

    if t.kind == 'RPAREN':
        raise LispError(f'Unexpected ) at {t.line}:{t.col}')

    raise LispError(f'Unknown token {t!r}')


# ─────────────────────────────────────────────────────────────
#  EVALUATOR
#
#  Uses an iterative loop for proper tail-call optimisation (TCO).
#  Tail calls replace `expr` and `env` and `continue` the loop
#  instead of recursing — so (fact 100000) won't blow the stack.
# ─────────────────────────────────────────────────────────────

def evaluate(expr: Any, env: Env) -> Any:
    while True:

        # ── Self-evaluating atoms ────────────────────────
        if isinstance(expr, (int, float, bool, LispStr)):
            return expr
        if expr is NIL or expr == []:
            return NIL

        # ── Symbol lookup ────────────────────────────────
        if isinstance(expr, Symbol):
            return env.lookup(str(expr))

        # ── Non-list (shouldn't happen, but be safe) ─────
        if not isinstance(expr, list) or not expr:
            return expr

        head = expr[0]

        # ─────────────────────────────────────────────────
        #  SPECIAL FORMS
        # ─────────────────────────────────────────────────

        if isinstance(head, Symbol):

            sf = str(head)

            # (quote datum)
            if sf == 'quote':
                return expr[1]

            # (if test consequent [alternate])
            if sf == 'if':
                test = evaluate(expr[1], env)
                if test is not False and test != []:
                    expr = expr[2]           # tail call: consequent
                else:
                    expr = expr[3] if len(expr) > 3 else NIL   # tail: alternate
                continue

            # (cond (test body...)* [(else body...)])
            if sf == 'cond':
                for clause in expr[1:]:
                    tst, *body = clause
                    if isinstance(tst, Symbol) and str(tst) == 'else':
                        expr = [Symbol('begin')] + body; break
                    val = evaluate(tst, env)
                    if val is not False and val != []:
                        if not body: return val
                        expr = [Symbol('begin')] + body; break
                else:
                    return NIL
                continue

            # (and e...)
            if sf == 'and':
                if len(expr) == 1: return True
                for e in expr[1:-1]:
                    v = evaluate(e, env)
                    if v is False or v == []: return False
                expr = expr[-1]; continue

            # (or e...)
            if sf == 'or':
                if len(expr) == 1: return False
                for e in expr[1:-1]:
                    v = evaluate(e, env)
                    if v is not False and v != []: return v
                expr = expr[-1]; continue

            # (begin e...)
            if sf == 'begin':
                if len(expr) == 1: return NIL
                for e in expr[1:-1]: evaluate(e, env)
                expr = expr[-1]; continue

            # (when test body...)
            if sf == 'when':
                if evaluate(expr[1], env) is not False:
                    for e in expr[2:-1]: evaluate(e, env)
                    expr = expr[-1]; continue
                return NIL

            # (unless test body...)
            if sf == 'unless':
                if evaluate(expr[1], env) is False:
                    for e in expr[2:-1]: evaluate(e, env)
                    expr = expr[-1]; continue
                return NIL

            # (define name val) or (define (name params...) body...)
            if sf == 'define':
                sig = expr[1]
                if isinstance(sig, list):                   # (define (f x y) body)
                    fname  = sig[0]
                    params = sig[1:]
                    body   = expr[2:]
                    fn = Lambda(params, body, env, name=str(fname))
                    env.define(str(fname), fn)
                    return Symbol(str(fname))
                else:                                        # (define x val)
                    val = evaluate(expr[2], env) if len(expr) > 2 else NIL
                    if isinstance(val, Lambda) and val.name is None:
                        val.name = str(sig)
                    env.define(str(sig), val)
                    return Symbol(str(sig))

            # (set! name val)
            if sf == 'set!':
                env.set(str(expr[1]), evaluate(expr[2], env))
                return NIL

            # (lambda (params...) body...)
            if sf == 'lambda':
                params = expr[1]
                body   = expr[2:]
                return Lambda(params, body, env)

            # (let ((v e)...) body...)   parallel bindings
            if sf == 'let':
                # Named let: (let name ((v e)...) body...)
                if isinstance(expr[1], Symbol):
                    name     = expr[1]
                    bindings = expr[2]; body = expr[3:]
                    inner    = Env(outer=env)
                    params   = [b[0] for b in bindings]
                    vals     = [evaluate(b[1], env) for b in bindings]
                    fn       = Lambda(params, body, inner, name=str(name))
                    inner.define(str(name), fn)
                    inner2   = Env(params, vals, inner)
                    for e in body[:-1]: evaluate(e, inner2)
                    expr = body[-1]; env = inner2; continue
                # Standard let
                bindings = expr[1]; body = expr[2:]
                inner    = Env(outer=env)
                for name, val_expr in bindings:
                    inner.define(str(name), evaluate(val_expr, env))
                for e in body[:-1]: evaluate(e, inner)
                expr = body[-1]; env = inner; continue

            # (let* ((v e)...) body...)  sequential bindings
            if sf == 'let*':
                bindings = expr[1]; body = expr[2:]
                inner    = Env(outer=env)
                for name, val_expr in bindings:
                    inner.define(str(name), evaluate(val_expr, inner))
                for e in body[:-1]: evaluate(e, inner)
                expr = body[-1]; env = inner; continue

            # (letrec ((v e)...) body...)
            if sf == 'letrec':
                bindings = expr[1]; body = expr[2:]
                inner    = Env(outer=env)
                for name, _ in bindings:
                    inner.define(str(name), NIL)
                for name, val_expr in bindings:
                    inner.define(str(name), evaluate(val_expr, inner))
                for e in body[:-1]: evaluate(e, inner)
                expr = body[-1]; env = inner; continue

            # (do ((var init step)...) (test result...) body...)
            # Basic do loop
            if sf == 'do':
                var_specs = expr[1]  # ((var init step)...)
                term      = expr[2]  # (test result...)
                cmd_body  = expr[3:]
                inner     = Env(outer=env)
                for spec in var_specs:
                    v, init = spec[0], spec[1]
                    inner.define(str(v), evaluate(init, env))
                while True:
                    test_val = evaluate(term[0], inner)
                    if test_val is not False and test_val != []:
                        # Terminal: eval result exprs
                        if len(term) > 1:
                            for r in term[1:-1]: evaluate(r, inner)
                            expr = term[-1]; env = inner
                        else:
                            return NIL
                        break
                    for e in cmd_body: evaluate(e, inner)
                    # Step
                    new_vals = {str(spec[0]): evaluate(spec[2] if len(spec)>2 else spec[0], inner)
                                for spec in var_specs}
                    for k, v in new_vals.items():
                        inner.define(k, v)
                continue

        # ─────────────────────────────────────────────────
        #  PROCEDURE APPLICATION
        # ─────────────────────────────────────────────────
        fn   = evaluate(head, env)
        args = [evaluate(a, env) for a in expr[1:]]

        if callable(fn):          # built-in
            return fn(*args)

        if isinstance(fn, Lambda):
            _check_arity(fn, args)
            inner = Env(fn.params, args, fn.env)
            for e in fn.body[:-1]:
                evaluate(e, inner)
            expr = fn.body[-1]; env = inner   # TCO: tail call → loop
            continue

        raise LispError(f"Not a procedure: {display(fn)!r}")


def _check_arity(fn: Lambda, args: list) -> None:
    params = fn.params
    # Check for rest parameter (variadic)
    if isinstance(params, Symbol):
        return   # (lambda args body) — accepts any number
    try:
        dot_idx = [str(p) for p in params].index('.')
        if len(args) < dot_idx:
            raise LispError(
                f'{fn.name or "lambda"}: expected ≥{dot_idx} args, got {len(args)}')
        return
    except ValueError:
        pass
    if len(args) != len(params):
        raise LispError(
            f'{fn.name or "lambda"}: expected {len(params)} arg(s), got {len(args)}')


# ─────────────────────────────────────────────────────────────
#  DISPLAY  (Lisp printer)
# ─────────────────────────────────────────────────────────────

def display(val: Any) -> str:
    if val is True:            return '#t'
    if val is False:           return '#f'
    if val is NIL or val == []: return '()'
    if isinstance(val, LispStr): return repr(val)
    if isinstance(val, Symbol):  return str(val)
    if isinstance(val, bool):    return '#t' if val else '#f'   # safety
    if isinstance(val, int):     return str(val)
    if isinstance(val, float):
        if val == int(val) and abs(val) < 1e15:
            return str(int(val))
        return str(val)
    if isinstance(val, list):
        return '(' + ' '.join(display(x) for x in val) + ')'
    if isinstance(val, Lambda):
        return f'#<lambda {val.name or ""}>'
    if callable(val):
        return f'#<builtin {getattr(val, "__name__", "?")}>'
    return str(val)


# ─────────────────────────────────────────────────────────────
#  BUILT-IN PROCEDURES
# ─────────────────────────────────────────────────────────────

def _num(*args):
    for a in args:
        if not isinstance(a, (int, float)) or isinstance(a, bool):
            raise LispError(f'Expected number, got {display(a)!r}')


def _mk_arith(op_name):
    def fn(*args):
        _num(*args)
        if op_name == '+': return sum(args) if args else 0
        if op_name == '*':
            r = 1
            for a in args: r *= a
            return r
        if op_name == '-':
            if len(args) == 0: raise LispError('- requires ≥1 argument')
            if len(args) == 1: return -args[0]
            return args[0] - sum(args[1:])
        if op_name == '/':
            if len(args) == 0: raise LispError('/ requires ≥1 argument')
            if len(args) == 1: return 1 / args[0]
            r = args[0]
            for a in args[1:]:
                if a == 0: raise LispError('Division by zero')
                r /= a
            return r
    fn.__name__ = op_name
    return fn


def _mk_cmp(op):
    def fn(*args):
        _num(*args)
        return all(op(args[i], args[i+1]) for i in range(len(args)-1))
    fn.__name__ = op.__name__
    return fn


def _mod(a, b):
    _num(a, b)
    if b == 0: raise LispError('Modulo by zero')
    return a % b if isinstance(a, int) and isinstance(b, int) else math.fmod(a, b)


def _deep_equal(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, LispStr) and isinstance(b, LispStr):
        return a.s == b.s
    return a == b


def _cons(a, b):
    if isinstance(b, list): return [a] + b
    raise LispError(f'cons: second arg must be a list, got {display(b)!r}')


def _car(x):
    if isinstance(x, list) and x: return x[0]
    raise LispError(f'car: expected non-empty list, got {display(x)!r}')


def _cdr(x):
    if isinstance(x, list) and x: return x[1:]
    raise LispError(f'cdr: expected non-empty list, got {display(x)!r}')


def _apply(fn, *args):
    last = args[-1]
    if not isinstance(last, list):
        raise LispError('apply: last argument must be a list')
    all_args = list(args[:-1]) + last
    if callable(fn): return fn(*all_args)
    if isinstance(fn, Lambda):
        _check_arity(fn, all_args)
        inner = Env(fn.params, all_args, fn.env)
        for e in fn.body[:-1]: evaluate(e, inner)
        return evaluate(fn.body[-1], inner)
    raise LispError(f'apply: not a procedure: {display(fn)!r}')


def _call_fn(fn, *args):
    """Helper to call Lambda or builtin with given args."""
    if callable(fn): return fn(*args)
    if isinstance(fn, Lambda):
        _check_arity(fn, list(args))
        inner = Env(fn.params, args, fn.env)
        for e in fn.body[:-1]: evaluate(e, inner)
        return evaluate(fn.body[-1], inner)
    raise LispError(f'Not callable: {display(fn)!r}')


def _map(fn, *lsts):
    if len(lsts) == 1:
        return [_call_fn(fn, x) for x in lsts[0]]
    return [_call_fn(fn, *xs) for xs in zip(*lsts)]


def _filter(fn, lst):
    return [x for x in lst if _call_fn(fn, x) is not False and _call_fn(fn, x) != []]


def _reduce(fn, lst, *init):
    if init:
        acc = init[0]; rest = lst
    elif lst:
        acc = lst[0]; rest = lst[1:]
    else:
        raise LispError('reduce: empty list with no initial value')
    for x in rest:
        acc = _call_fn(fn, acc, x)
    return acc


def _shuffle(lst):
    if not isinstance(lst, list):
        raise LispError(f'shuffle: expected list, got {display(lst)!r}')
    out = list(lst)
    random.shuffle(out)
    return out


def _random_choice(lst):
    if not isinstance(lst, list):
        raise LispError(f'random-choice: expected list, got {display(lst)!r}')
    if not lst:
        raise LispError('random-choice: empty list')
    return random.choice(lst)


# ─────────────────────────────────────────────────────────────
#  STANDARD LIBRARY  (implemented in Lisp itself)
# ─────────────────────────────────────────────────────────────

STDLIB = """\
(define (not x)        (if x #f #t))
(define (boolean? x)   (or (eq? x #t) (eq? x #f)))
(define (zero? n)      (= n 0))
(define (positive? n)  (> n 0))
(define (negative? n)  (< n 0))
(define (even? n)      (= (mod n 2) 0))
(define (odd? n)       (not (even? n)))
(define (abs x)        (if (< x 0) (- x) x))
(define (square x)     (* x x))
(define (cube x)       (* x x x))
(define (cadr x)       (car (cdr x)))
(define (caddr x)      (car (cdr (cdr x))))
(define (cadddr x)     (car (cdr (cdr (cdr x)))))
(define (caar x)       (car (car x)))
(define (cadar x)      (car (cdr (car x))))
(define (list? x)
  (or (null? x) (and (pair? x) (list? (cdr x)))))
(define (list-copy lst) (append lst '()))
(define (iota n)
  (define (iter i acc)
    (if (< i 0) acc (iter (- i 1) (cons i acc))))
  (iter (- n 1) '()))
(define (range start end)
  (if (>= start end) '()
      (cons start (range (+ start 1) end))))
(define (fold-left f init lst)
  (if (null? lst) init
      (fold-left f (f init (car lst)) (cdr lst))))
(define (fold-right f init lst)
  (if (null? lst) init
      (f (car lst) (fold-right f init (cdr lst)))))
(define (for-each f lst)
  (if (null? lst) '()
      (begin (f (car lst)) (for-each f (cdr lst)))))
(define (any pred lst)
  (cond ((null? lst) #f)
        ((pred (car lst)) #t)
        (else (any pred (cdr lst)))))
(define (every pred lst)
  (cond ((null? lst) #t)
        ((not (pred (car lst))) #f)
        (else (every pred (cdr lst)))))
(define (sum lst)      (fold-left + 0 lst))
(define (product lst)  (fold-left * 1 lst))
(define (flatten lst)
  (cond ((null? lst) '())
        ((pair? (car lst))
         (append (flatten (car lst)) (flatten (cdr lst))))
        (else (cons (car lst) (flatten (cdr lst))))))
(define (take lst n)
  (if (or (null? lst) (= n 0)) '()
      (cons (car lst) (take (cdr lst) (- n 1)))))
(define (drop lst n)
  (if (or (null? lst) (= n 0)) lst
      (drop (cdr lst) (- n 1))))
(define (compose f g)  (lambda (x) (f (g x))))
(define (curry f x)    (lambda args (apply f (cons x args))))
(define (flip f)       (lambda (a b) (f b a)))
(define (identity x)   x)
(define (const x)      (lambda _ x))
(define (sort lst less?)
  (if (or (null? lst) (null? (cdr lst))) lst
      (let ((pivot (car lst)) (rest (cdr lst)))
        (append
          (sort (filter (lambda (x) (less? x pivot)) rest) less?)
          (list pivot)
          (sort (filter (lambda (x) (not (less? x pivot))) rest) less?)))))
(define (sort-numbers lst) (sort lst <))
(define (make-counter)
  (let ((n 0))
    (lambda ()
      (set! n (+ n 1))
      n)))
(define (make-adder n)  (lambda (x) (+ x n)))
(define (assoc-get key alist default)
  (let ((pair (assoc key alist)))
    (if pair (cadr pair) default)))
(define (zip-with f a b)
  (if (or (null? a) (null? b)) '()
      (cons (f (car a) (car b))
            (zip-with f (cdr a) (cdr b)))))
"""


# ─────────────────────────────────────────────────────────────
#  GLOBAL ENVIRONMENT  (built-ins + stdlib)
# ─────────────────────────────────────────────────────────────

def make_env() -> Env:
    e = Env()

    # Arithmetic
    e.define('+',    _mk_arith('+'))
    e.define('-',    _mk_arith('-'))
    e.define('*',    _mk_arith('*'))
    e.define('/',    _mk_arith('/'))
    e.define('mod',  _mod)
    e.define('%',    _mod)
    e.define('remainder', lambda a,b: int(math.remainder(a,b)) if isinstance(a,int) and isinstance(b,int) else math.remainder(a,b))
    e.define('quotient',  lambda a,b: int(a/b))
    e.define('expt',      lambda a,b: (_num(a,b), a**b)[1])

    # Comparisons
    e.define('=',   _mk_cmp(operator.eq))
    e.define('<',   _mk_cmp(operator.lt))
    e.define('>',   _mk_cmp(operator.gt))
    e.define('<=',  _mk_cmp(operator.le))
    e.define('>=',  _mk_cmp(operator.ge))
    e.define('eq?',     lambda a,b: a is b or a == b)
    e.define('eqv?',    lambda a,b: a is b or a == b)
    e.define('equal?',  _deep_equal)
    e.define('not',     lambda x: x is False or x == [])

    # Math
    e.define('sqrt',     lambda x: (_num(x), (int(r) if (r:=math.sqrt(x))==int(r) else r))[1])
    e.define('floor',    lambda x: (_num(x), int(math.floor(x)))[1])
    e.define('ceiling',  lambda x: (_num(x), int(math.ceil(x)))[1])
    e.define('round',    lambda x: (_num(x), int(round(x)))[1])
    e.define('truncate', lambda x: (_num(x), int(math.trunc(x)))[1])
    e.define('abs',      lambda x: (_num(x), -x if x < 0 else x)[1])
    e.define('max',      lambda *a: (_num(*a), max(a))[1])
    e.define('min',      lambda *a: (_num(*a), min(a))[1])
    e.define('gcd',      lambda a,b: math.gcd(int(abs(a)),int(abs(b))))
    e.define('lcm',      lambda a,b: (lambda g: 0 if g==0 else abs(int(a)*int(b))//g)(math.gcd(int(abs(a)),int(abs(b)))))
    e.define('sin',  math.sin); e.define('cos', math.cos); e.define('tan', math.tan)
    e.define('asin', math.asin); e.define('acos', math.acos); e.define('atan', math.atan)
    e.define('exp',  math.exp); e.define('log', math.log)
    e.define('pi',   math.pi); e.define('e',   math.e)
    e.define('number->exact', int)
    e.define('exact->inexact', float)
    e.define('inexact->exact', int)

    # Type predicates
    e.define('number?',    lambda x: isinstance(x,(int,float)) and not isinstance(x,bool))
    e.define('integer?',   lambda x: isinstance(x,int) and not isinstance(x,bool))
    e.define('real?',      lambda x: isinstance(x,(int,float)) and not isinstance(x,bool))
    e.define('string?',    lambda x: isinstance(x, LispStr))
    e.define('symbol?',    lambda x: isinstance(x, Symbol))
    e.define('pair?',      lambda x: isinstance(x, list) and len(x) > 0)
    e.define('null?',      lambda x: x is NIL or x == [])
    e.define('list?',      lambda x: isinstance(x, list))
    e.define('boolean?',   lambda x: isinstance(x, bool))
    e.define('procedure?', lambda x: callable(x) or isinstance(x, Lambda))
    e.define('char?',      lambda x: isinstance(x, LispStr) and len(x.s)==1)

    # List operations
    e.define('cons',       _cons)
    e.define('car',        _car)
    e.define('cdr',        _cdr)
    e.define('list',       lambda *a: list(a))
    e.define('length',     lambda x: len(x) if isinstance(x,list) else (_ for _ in ()).throw(LispError('length: not a list')))
    e.define('append',     lambda *a: sum(a, []) if all(isinstance(x,list) for x in a) else (_ for _ in ()).throw(LispError('append: not a list')))
    e.define('reverse',    lambda x: list(reversed(x)) if isinstance(x,list) else (_ for _ in ()).throw(LispError('reverse: not a list')))
    e.define('list-ref',   lambda lst,i: lst[int(i)])
    e.define('list-tail',  lambda lst,i: lst[int(i):])
    e.define('last',       lambda lst: lst[-1] if lst else NIL)
    e.define('assoc',      lambda k,lst: next((p for p in lst if _deep_equal(p[0],k)), False))
    e.define('assq',       lambda k,lst: next((p for p in lst if p[0]==k), False))
    e.define('member',     lambda x,lst: next((lst[i:] for i,v in enumerate(lst) if _deep_equal(v,x)), False))
    e.define('memq',       lambda x,lst: next((lst[i:] for i,v in enumerate(lst) if v==x), False))
    e.define('map',        _map)
    e.define('filter',     _filter)
    e.define('reduce',     _reduce)
    e.define('shuffle',    _shuffle)
    e.define('random-choice', _random_choice)
    e.define('fold-left',  _reduce)
    e.define('for-each',   lambda fn,lst: [_call_fn(fn,x) for x in lst] and NIL)
    e.define('apply',      _apply)

    # Strings
    e.define('string',           lambda *cs: LispStr(''.join(c.s for c in cs)))
    e.define('string-append',    lambda *a: LispStr(''.join(x.s for x in a)))
    e.define('string-length',    lambda s: len(s.s))
    e.define('substring',        lambda s,a,b=None: LispStr(s.s[int(a):] if b is None else s.s[int(a):int(b)]))
    e.define('string-ref',       lambda s,i: LispStr(s.s[int(i)]))
    e.define('string->list',     lambda s: [LispStr(c) for c in s.s])
    e.define('list->string',     lambda lst: LispStr(''.join(c.s for c in lst)))
    e.define('string-upcase',    lambda s: LispStr(s.s.upper()))
    e.define('string-downcase',  lambda s: LispStr(s.s.lower()))
    e.define('string-contains',  lambda s,p: False if p.s not in s.s else s.s.index(p.s))
    e.define('string=?',  lambda a,b: a.s==b.s)
    e.define('string<?',  lambda a,b: a.s<b.s)
    e.define('string>?',  lambda a,b: a.s>b.s)
    e.define('number->string', lambda n: LispStr(str(int(n)) if isinstance(n,float) and n==int(n) else str(n)))
    e.define('string->number', lambda s: (lambda v: v)(int(s.s) if s.s.lstrip('-').isdigit() else (float(s.s) if _is_float(s.s) else False)))
    e.define('symbol->string',  lambda s: LispStr(str(s)))
    e.define('string->symbol',  lambda s: Symbol(s.s))

    # I/O
    _out = []
    def _display(*args):
        for a in args:
            _out.append(a.s if isinstance(a,LispStr) else display(a))
        return NIL
    def _newline():
        _out.append('\n'); return NIL
    e.define('display',  _display)
    e.define('newline',  _newline)
    e.define('write',    lambda x: _display(LispStr(display(x))))
    e.define('error',    lambda *a: (_ for _ in ()).throw(LispError(' '.join(x.s if isinstance(x,LispStr) else display(x) for x in a))))
    e.define('print',    lambda x: (print(display(x)), NIL)[1])
    e.define('get-output', lambda: LispStr(''.join(_out)))
    e.define('clear-output', lambda: _out.clear() or NIL)

    # Values
    e.define('#t',   True)
    e.define('#f',   False)
    e.define('nil',  NIL)
    e.define('else', True)
    e.define('void', NIL)

    # Load Lisp stdlib
    for expr in parse(STDLIB):
        evaluate(expr, e)

    return e


def _is_float(s: str) -> bool:
    try: float(s); return True
    except ValueError: return False


# ─────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────

def run(src: str, env: Env) -> str:
    """Evaluate source, return display string of last result."""
    try:
        exprs = parse(src)
        if not exprs:
            return ''
        result = NIL
        for e in exprs:
            result = evaluate(e, env)
        return display(result)
    except LispError as ex:
        return f'Error: {ex}'
    except RecursionError:
        return 'Error: maximum recursion depth — try tail-recursive style'
    except ZeroDivisionError:
        return 'Error: division by zero'
    except Exception as ex:
        return f'Error: {ex}'


# ─────────────────────────────────────────────────────────────
#  TEST SUITE
# ─────────────────────────────────────────────────────────────

TESTS = [
    # Arithmetic
    ('(+ 1 2)',             '3'),
    ('(* 6 7)',             '42'),
    ('(- 10 3)',            '7'),
    ('(/ 10 2)',            '5'),
    ('(mod 17 5)',          '2'),
    ('(+ 1 2 3 4 5)',       '15'),
    ('(- 5)',               '-5'),
    # Booleans & logic
    ('(if #t 1 2)',         '1'),
    ('(if #f 1 2)',         '2'),
    ('(not #f)',            '#t'),
    ('(and 1 2 3)',         '3'),
    ('(and 1 #f 3)',        '#f'),
    ('(or #f #f 5)',        '5'),
    ('(or #f #f #f)',       '#f'),
    # Comparisons
    ('(< 1 2 3)',           '#t'),
    ('(> 3 2 1)',           '#t'),
    ('(= 2 2 2)',           '#t'),
    # Define / lambda
    ('(define x 42) x',                                 '42'),
    ('(define (sq x) (* x x)) (sq 9)',                   '81'),
    ('((lambda (x y) (+ x y)) 3 4)',                     '7'),
    # Closures
    ('(define (make-adder n) (lambda (x) (+ x n)))'
     '(define add5 (make-adder 5)) (add5 10)',           '15'),
    # set!
    ('(define n 0) (set! n 42) n',                       '42'),
    # Recursion
    ('(define (fact n) (if (= n 0) 1 (* n (fact (- n 1))))) (fact 10)', '3628800'),
    ('(define (fib n) (if (< n 2) n (+ (fib (- n 1)) (fib (- n 2))))) (fib 10)', '55'),
    # Tail recursion (large n — would stack-overflow without TCO)
    ('(define (loop n) (if (= n 0) "done" (loop (- n 1)))) (loop 100000)', '"done"'),
    # Lists
    ("(car '(1 2 3))",             '1'),
    ("(cdr '(1 2 3))",             '(2 3)'),
    ("(cons 0 '(1 2))",            '(0 1 2)'),
    ("(length '(a b c d))",        '4'),
    ("(append '(1 2) '(3 4))",     '(1 2 3 4)'),
    ("(reverse '(1 2 3))",         '(3 2 1)'),
    # Higher-order
    ("(map (lambda (x) (* x x)) '(1 2 3 4 5))", '(1 4 9 16 25)'),
    ("(filter odd? '(1 2 3 4 5 6))",             '(1 3 5)'),
    ("(reduce + '(1 2 3 4 5))",                  '15'),
    # Let
    ('(let ((a 1) (b 2)) (+ a b))',              '3'),
    ('(let* ((a 1) (b (+ a 1))) b)',             '2'),
    # Cond
    ('(cond (#f 0) (#t 1) (else 2))',            '1'),
    ('(cond (#f 0) (#f 1) (else 99))',           '99'),
    # Stdlib
    ('(iota 5)',                   '(0 1 2 3 4)'),
    ('(sum (iota 5))',             '10'),
    ('(even? 4)',                  '#t'),
    ('(odd? 7)',                   '#t'),
    ('(abs -42)',                  '42'),
    ('(square 9)',                 '81'),
    ("(sort-numbers '(3 1 4 1 5 9))", '(1 1 3 4 5 9)'),
    ("(length (shuffle '(a b c d e)))", '5'),
    ("(if (member (random-choice '(red blue green)) '(red blue green)) #t #f)", '#t'),
    # Strings
    ('(string-append "hello" " " "world")', '"hello world"'),
    ('(string-length "abc")',               '3'),
    ('(number->string 42)',                 '"42"'),
    # Named let (loop idiom)
    ('(let loop ((i 0) (acc 0))'
     '  (if (> i 10) acc (loop (+ i 1) (+ acc i))))', '55'),
]


def run_tests() -> bool:
    env = make_env()
    passed = 0; failed = []
    for src, expected in TESTS:
        got = run(src, env)
        if got == expected:
            passed += 1
        else:
            failed.append((src, expected, got))
    print(f'Tests: {passed}/{len(TESTS)} passed')
    if failed:
        print('FAILURES:')
        for src, exp, got in failed:
            src_short = src[:60] + ('...' if len(src)>60 else '')
            print(f'  {src_short!r}')
            print(f'    expected: {exp!r}')
            print(f'    got:      {got!r}')
    return not failed


# ─────────────────────────────────────────────────────────────
#  INTERACTIVE REPL
# ─────────────────────────────────────────────────────────────

def repl():
    print("Wesley's Lisp  |  type (help) for examples, Ctrl-D to exit")
    env = make_env()

    def _help():
        print("""\
Examples:
  (+ 1 2 3)                               ; 6
  (define (fact n)
    (if (= n 0) 1 (* n (fact (- n 1)))))
  (fact 10)                               ; 3628800
  (map (lambda (x) (* x x)) (iota 6))    ; (0 1 4 9 16 25)
  (filter even? (iota 10))               ; (0 2 4 6 8)
  (define (fib n)
    (if (< n 2) n (+ (fib (- n 1)) (fib (- n 2)))))
  (fib 15)                               ; 610
  (define (make-counter)
    (let ((n 0)) (lambda () (set! n (+ n 1)) n)))
  (define c (make-counter))
  (c) (c) (c)                            ; 1 2 3
  (shuffle '(alpha beta gamma delta))    ; random order
  (random-choice '(tea coffee raktajino)); pick one""")
        return NIL
    env.define('help', _help)

    buf = ''
    while True:
        try:
            prompt = '... ' if buf.strip() else 'lisp> '
            line   = input(prompt)
            buf   += line + '\n'
            # Keep reading until parens are balanced
            depth = 0
            for ch in buf:
                if ch == '(':  depth += 1
                elif ch == ')': depth -= 1
            if depth > 0:
                continue
            src = buf.strip()
            if src:
                result = run(src, env)
                if result and result != '()':
                    print(f'=> {result}')
            buf = ''
        except EOFError:
            print('\nBye.'); break
        except KeyboardInterrupt:
            buf = ''; print()


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if '--test' in sys.argv:
        sys.exit(0 if run_tests() else 1)
    elif len(sys.argv) > 1 and sys.argv[1] != '--test':
        path = sys.argv[1]
        env  = make_env()
        with open(path) as f:
            result = run(f.read(), env)
        if result and result != '()':
            print(result)
    else:
        repl()
