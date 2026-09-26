"""Minimal Streamlit stand-in: enough to execute dashboard.py headlessly and
catch real errors (bad columns, bad maths, bad SQL shape) without a browser."""
import contextlib, sys, types

CALLS = []
WARNINGS = []


class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Col(_Ctx):
    def __getattr__(self, name):
        return getattr(sys.modules[__name__], name)


class _SessionState(dict):
    def __getattr__(self, k): return self.get(k)
    def __setattr__(self, k, v): self[k] = v


session_state = _SessionState()
secrets = {}


def _record(name):
    def f(*a, **k):
        CALLS.append(name)
        return None
    return f


def set_page_config(*a, **k): pass
def markdown(*a, **k): CALLS.append("markdown")
def caption(*a, **k): CALLS.append("caption")
def divider(*a, **k): pass
def subheader(*a, **k): CALLS.append("subheader")
def error(*a, **k): CALLS.append(("error", a))
def info(*a, **k): CALLS.append("info")
def warning(msg, *a, **k): WARNINGS.append(str(msg)); CALLS.append("warning")
def stop(): raise SystemExit("st.stop()")
def rerun(): raise RuntimeError("rerun called during headless run")
def progress(*a, **k): CALLS.append("progress")
def dataframe(df=None, *a, **k):
    CALLS.append("dataframe")
    # Force the Styler to actually compute, the way rendering would.
    if hasattr(df, "to_html"):
        df.to_html()
def line_chart(*a, **k): CALLS.append("line_chart")
def bar_chart(*a, **k): CALLS.append("bar_chart")
def download_button(*a, **k): CALLS.append("download_button")
def button(*a, **k): return False
import os
AGGRESSIVE = os.environ.get("FAKE_ST_AGGRESSIVE") == "1"


def checkbox(label, value=False, **k):
    return (not value) if AGGRESSIVE else value
def slider(label, lo, hi, default=None, step=None, **k): return default if default is not None else lo
def selectbox(label, options, index=0, **k):
    opts = list(options)
    if not opts:
        return None
    if AGGRESSIVE and len(opts) > 1:
        return opts[-1]
    return opts[index]
def multiselect(label, options, default=None, **k):
    opts = list(options)
    if AGGRESSIVE:
        return opts[:1]          # narrow to a single choice
    return list(default) if default is not None else []
def number_input(label, min_value=None, value=None, step=None, placeholder=None, **k):
    if AGGRESSIVE and value is None:
        return 0.0 if "min" in label.lower() else 1e9
    return value
def text_input(label, value="", **k): return value
def date_input(label, value=None, **k): return value
def columns(n, **k):
    n = n if isinstance(n, int) else len(n)
    return [_Col() for _ in range(n)]
def tabs(names): return [_Ctx() for _ in names]
def expander(*a, **k): return _Ctx()
def metric(*a, **k): CALLS.append("metric")


def cache_data(*dargs, **dkw):
    def deco(fn): return fn
    if dargs and callable(dargs[0]):
        return dargs[0]
    return deco


def cache_resource(*dargs, **dkw):
    def deco(fn): return fn
    if dargs and callable(dargs[0]):
        return dargs[0]
    return deco
