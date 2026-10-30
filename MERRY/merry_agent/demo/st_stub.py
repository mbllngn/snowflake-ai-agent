"""A stand-in `streamlit` module for the gates (step 12c polish): every call is accepted and returns something
renderable, widgets return their default, buttons are not pressed, fragments and caches pass through. Lets a gate run
review.py's real render path, with real Snowpark Rows, and fail on any exception in our code."""
import sys, types


class _Ctx:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(mod, name)

    def __call__(self, *a, **k):
        return _Ctx()


class _State(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v


mod = types.ModuleType("streamlit")
mod.calls = []                                     # (name, first argument) of every output call, for checks
mod.session_state = _State()
mod.user = {}


def _out(name):
    def f(*a, **k):
        mod.calls.append((name, a[0] if a else None))
        return _Ctx()
    return f


for n in ("markdown", "caption", "write", "text", "code", "json", "error", "warning", "info", "success", "dataframe",
          "download_button", "link_button", "toast", "divider", "subheader", "header", "title", "metric", "table", "image", "pdf",
          "bar_chart", "line_chart", "area_chart"):
    setattr(mod, n, _out(n))
for n in ("expander", "status", "chat_message", "container", "spinner", "popover", "form", "empty", "sidebar", "tabs_ctx"):
    setattr(mod, n, _Ctx)
mod.sidebar = _Ctx()                               # used as `with st.sidebar:` (an object, not a call)
mod.columns = lambda spec, **k: [_Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]
mod.tabs = lambda names, **k: [_Ctx() for _ in names]
mod.button = lambda *a, **k: False
mod.form_submit_button = lambda *a, **k: False
mod.checkbox = lambda *a, value=False, **k: value
mod.toggle = mod.checkbox
mod.text_input = lambda label, value="", **k: value
mod.text_area = lambda label, value="", **k: value
mod.number_input = lambda label, *a, value=0, **k: value
mod.selectbox = lambda label, options, index=0, format_func=str, **k: (
    [format_func(o) for o in options], list(options)[index] if list(options) else None)[1]
mod.radio = mod.selectbox
mod.pills = lambda label, options, default=None, **k: default
mod.segmented_control = mod.pills
mod.multiselect = lambda label, options, default=None, **k: default or []
mod.data_editor = lambda data, **k: data
mod.chat_input = lambda *a, **k: None
mod.file_uploader = lambda *a, **k: None
mod.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
mod.cache_resource = mod.cache_data
mod.fragment = lambda *a, **k: (lambda f: (lambda *x, **y: None))
mod.progress = lambda *a, **k: _Ctx()
mod.rerun = lambda *a, **k: None
mod.stop = lambda: None
mod.set_page_config = lambda *a, **k: None
mod.selection = types.SimpleNamespace(rows=[])     # st.dataframe(on_select=...) event: nothing selected
mod.column_config = types.SimpleNamespace(**{n: (lambda *a, **k: None) for n in
                                              ("TextColumn", "NumberColumn", "SelectboxColumn", "DateColumn",
                                               "CheckboxColumn", "LinkColumn", "Column", "ProgressColumn")})


def install():
    sys.modules["streamlit"] = mod
    return mod
