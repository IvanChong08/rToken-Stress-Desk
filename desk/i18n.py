"""
中英切换。

用法就一个函数: `t("中文", "English")` —— 两种语言就地成对写在一起。
不用 key + 词表, 是因为词表很容易和代码走样 (改了中文忘了改 key, 或者反过来),
而这个项目里几乎每句话都带数字格式串, 就地成对最不容易出错:

    t("最差 %s · 亏掉 %.1f%% 的本金", "Worst %s · lost %.1f%% of principal") % (date, pct)

语言是模块级全局变量: Streamlit 每次交互都重跑整个脚本, 在脚本开头 set_lang() 一次即可。
⚠️ 已经生成好的报告 (存在 session_state 里) 是当时那个语言的文字, 切换语言后要重新生成。
"""
from __future__ import annotations

LANGS = {"zh": "中文", "en": "English"}
_lang = "zh"


def set_lang(lang: str) -> str:
    global _lang
    _lang = lang if lang in LANGS else "zh"
    return _lang


def get_lang() -> str:
    return _lang


def t(zh: str, en: str) -> str:
    return en if _lang == "en" else zh


def join(items, zh_sep: str = "、", en_sep: str = ", ") -> str:
    return (en_sep if _lang == "en" else zh_sep).join(items)
