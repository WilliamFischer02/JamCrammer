"""Jamaa Night design system: tokens in one place, QSS generated from them (KICKOFF §12.1).

No widget sets an inline color. Everything reads ``PALETTE`` or uses an object name / property
that the generated stylesheet targets. Pages have a **kind** (home, bot, tool, system) and an
**accent**: every bot has its own colour (Best Guess violet, Arcade blue, Adventure amber,
Opportunist green, Den Planner teal), tools share cyan, system pages are muted. The accent
shows on the nav rail, the page's run panel, its meters and its primary button, so a page
reads as one thing at a glance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str = "#0E1116"
    surface: str = "#151A22"
    raised: str = "#1C2330"
    border: str = "#283142"
    text: str = "#E7EAF0"
    muted: str = "#97A1B3"
    bestguess: str = "#9B6BFF"  # gem violet
    arcade: str = "#3AA0FF"  # reef blue
    opportunist: str = "#2FD08A"  # jungle green
    adventure: str = "#FFA94D"  # torch amber
    planner: str = "#2DD4BF"  # den teal
    tool: str = "#4FD1C5"  # tools: cyan
    warn: str = "#F5B941"
    danger: str = "#FF5D5D"
    ok: str = "#2FD08A"
    idle: str = "#97A1B3"
    accent: str = "#9B6BFF"

    def for_mode(self, mode: str) -> str:
        return {
            "bestguess": self.bestguess,
            "hunt": self.bestguess,
            "arcade": self.arcade,
            "farm": self.arcade,
            "opportunist": self.opportunist,
            "adventure": self.adventure,
            "planner": self.planner,
        }.get(mode, self.accent)

    def for_page(self, key: str) -> str:
        kind = PAGE_KINDS.get(key, "tool")
        if kind == "bot":
            return self.for_mode(key)
        if kind == "tool":
            return self.tool
        if kind == "home":
            return self.text
        return self.muted

    def for_level(self, level: str) -> str:
        return {"HIGH": self.danger, "NOTICE": self.warn, "INFO": self.muted}.get(level, self.muted)

    def for_state(self, state: str) -> str:
        s = state.upper()
        if s.startswith("PAUSED"):
            return self.warn
        if s in ("ERROR", "KILLED", "FATAL"):
            return self.danger
        if s in ("IDLE", "STOPPED", ""):
            return self.idle
        return self.ok


PALETTE = Palette()

# page key -> kind; the nav rail groups them in this order
PAGE_KINDS: dict[str, str] = {
    "dashboard": "home",
    "hunt": "bot",
    "farm": "bot",
    "adventure": "bot",
    "opportunist": "bot",
    "planner": "bot",
    "mapping": "tool",
    "learn": "tool",
    "calibration": "tool",
    "answerbank": "tool",
    "parties": "tool",
    "profitlab": "tool",
    "notifications": "system",
    "settings": "system",
    "logs": "system",
}
NAV_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("", ("dashboard",)),
    ("Bots", ("hunt", "farm", "adventure", "opportunist", "planner")),
    ("Tools", ("mapping", "learn", "calibration", "answerbank", "parties", "profitlab")),
    ("System", ("notifications", "settings", "logs")),
]
ACCENTS: tuple[str, ...] = ("hunt", "farm", "adventure", "opportunist", "planner", "tool", "home")

FONT_FAMILY = "Segoe UI Variable"
FONT_FALLBACK = "Segoe UI"
FONT_SIZES = {"xs": 12, "sm": 13, "md": 15, "lg": 18, "xl": 24, "xxl": 32}
RADIUS = 12
GRID = 4
PAD_SM = 8
PAD_MD = 16
PAD_LG = 24
NAV_WIDTH = 208
NAV_COLLAPSED = 64
RUN_STRIP_HEIGHT = 56
BIG_BUTTON_HEIGHT = 44
HUD_SIZE = (320, 120)
ANIM_FAST_MS = 160
ANIM_MS = 240
ANIM_SLOW_MS = 400


def accent_color(key: str, p: Palette = PALETTE) -> str:
    return {"tool": p.tool, "home": p.text}.get(key, p.for_mode(key))


def _accent_rules(p: Palette) -> str:
    """Per-accent rules: the nav rail's checked bar, the run panel's edge, meters, big
    buttons. Generated from tokens, one block per accent key."""
    out = []
    for key in ACCENTS:
        c = accent_color(key, p)
        out.append(
            f"""
    #NavRail QToolButton[accent="{key}"]:checked {{ border-left: 3px solid {c}; color: {p.text}; }}
    #RunPanel[accent="{key}"] {{ border-left: 4px solid {c}; }}
    #RunStrip[accent="{key}"] {{ border-bottom: 2px solid {c}; }}
    QProgressBar[accent="{key}"]::chunk {{ background: {c}; }}
    QPushButton[kind="accent"][accent="{key}"] {{ background: {c}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="accent"][accent="{key}"]:hover {{ background: {c}; border: 2px solid {p.text}; }}
    QLabel[accent="{key}"] {{ color: {c}; }}
    #KindTag[accent="{key}"] {{ color: {c}; border: 1px solid {c}; }}
    """
        )
    return "".join(out)


def build_qss(p: Palette = PALETTE) -> str:
    """Generate the whole stylesheet from tokens."""
    return f"""
    QWidget {{
        background: {p.bg};
        color: {p.text};
        font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}";
        font-size: {FONT_SIZES["sm"]}px;
        selection-background-color: {p.accent};
        selection-color: {p.bg};
    }}
    QMainWindow, QDialog, QWizard {{ background: {p.bg}; }}
    QToolTip {{
        background: {p.raised}; color: {p.text}; border: 1px solid {p.border};
        padding: {PAD_SM}px; border-radius: {RADIUS // 2}px;
    }}

    /* --- nav rail --- */
    #NavRail {{ background: {p.surface}; border-right: 1px solid {p.border}; }}
    #NavRail QToolButton {{
        background: transparent; color: {p.muted}; border: none; border-left: 3px solid transparent;
        border-radius: {RADIUS // 2}px; padding: {PAD_SM}px {PAD_SM}px {PAD_SM}px {PAD_SM + 2}px;
        text-align: left; font-size: {FONT_SIZES["sm"]}px;
    }}
    #NavRail QToolButton:hover {{ background: {p.raised}; color: {p.text}; }}
    #NavRail QToolButton:checked {{ background: {p.raised}; color: {p.text}; }}
    #NavBrand {{ font-size: {FONT_SIZES["lg"]}px; font-weight: 600; color: {p.text}; padding: {PAD_SM}px; }}
    #NavGroup {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; font-weight: 600;
        padding: {PAD_MD}px 0 2px {PAD_SM}px; text-transform: uppercase; letter-spacing: 1px; }}

    /* --- run strip (only while a bot runs and its page is not the one shown) --- */
    #RunStrip {{ background: {p.surface}; border-bottom: 2px solid {p.border}; }}
    #StatePill {{
        background: {p.raised}; color: {p.text}; border: 1px solid {p.border};
        border-radius: 14px; padding: 4px {PAD_MD}px; font-size: {FONT_SIZES["sm"]}px; font-weight: 600;
        min-height: 20px;
    }}
    #StatePill[state="running"] {{ border-color: {p.ok}; color: {p.ok}; }}
    #StatePill[state="paused"] {{ border-color: {p.warn}; color: {p.warn}; }}
    #StatePill[state="error"] {{ border-color: {p.danger}; color: {p.danger}; }}
    #StatePill[state="idle"] {{ border-color: {p.border}; color: {p.muted}; }}
    #LeaseIndicator, #HotkeyHint {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; }}
    #StripTitle {{ font-weight: 600; font-size: {FONT_SIZES["md"]}px; }}

    /* --- cards & pages --- */
    #Page {{ background: {p.bg}; }}
    #PageTitle {{ font-size: {FONT_SIZES["xl"]}px; font-weight: 600; padding: 0; }}
    #PageSubtitle {{ color: {p.muted}; font-size: {FONT_SIZES["sm"]}px; }}
    #KindTag {{ border-radius: 9px; padding: 1px 8px; font-size: {FONT_SIZES["xs"]}px; font-weight: 600; }}
    QFrame#Card {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS}px;
    }}
    QFrame#RunPanel {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS}px;
    }}
    QLabel, QCheckBox, QRadioButton, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
    #CardTitle {{ font-size: {FONT_SIZES["md"]}px; font-weight: 600; }}
    #CardHelp {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; }}
    #SectionLabel {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }}
    #StatusLine {{ font-size: {FONT_SIZES["md"]}px; font-weight: 600; }}
    #StatusDetail {{ color: {p.muted}; font-size: {FONT_SIZES["sm"]}px; }}
    #StatValue {{ font-size: {FONT_SIZES["xxl"]}px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    #StatValue[size="md"] {{ font-size: {FONT_SIZES["xl"]}px; }}
    #StatLabel {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase; }}
    #MeterLabel {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase; }}
    #MeterValue {{ font-size: {FONT_SIZES["sm"]}px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    #Chip {{ border-radius: 9px; padding: 2px 8px; font-size: {FONT_SIZES["xs"]}px; font-weight: 600; }}
    #Chip[status="calibrated"] {{ background: {p.ok}; color: {p.bg}; }}
    #Chip[status="proposed"] {{ background: {p.warn}; color: {p.bg}; }}
    #Chip[status="uncalibrated"] {{ background: {p.danger}; color: {p.bg}; }}
    #Chip[status="neutral"] {{ background: {p.raised}; color: {p.text}; }}

    /* --- controls --- */
    QPushButton {{
        background: {p.raised}; color: {p.text}; border: 1px solid {p.border};
        border-radius: {RADIUS // 2}px; padding: 6px {PAD_MD}px; min-height: 20px;
    }}
    QPushButton:hover {{ border-color: {p.muted}; }}
    QPushButton:pressed {{ background: {p.border}; }}
    QPushButton:disabled {{ color: {p.muted}; border-color: {p.surface}; background: {p.surface}; }}
    QPushButton[size="big"] {{ min-height: {BIG_BUTTON_HEIGHT - 12}px; font-size: {FONT_SIZES["md"]}px; padding: 6px {PAD_LG}px; }}
    QPushButton[kind="primary"] {{ background: {p.accent}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="danger"] {{ background: {p.danger}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="ok"] {{ background: {p.ok}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid {p.border}; }}
    QPushButton[kind="ghost"]:hover {{ background: {p.raised}; }}
    QPushButton[kind="link"] {{ background: transparent; border: none; color: {p.accent}; padding: 0; text-decoration: underline; }}
    QPushButton[kind="danger"]:disabled, QPushButton[kind="ok"]:disabled, QPushButton[kind="accent"]:disabled {{
        background: {p.raised}; color: {p.muted}; border: 1px solid {p.border};
    }}
    QToolButton[kind="icon"] {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS // 2}px; padding: 4px; }}
    QToolButton[kind="icon"]:hover {{ background: {p.raised}; border-color: {p.border}; }}
    QToolButton[kind="icon"]:checked {{ background: {p.raised}; border-color: {p.muted}; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {p.raised}; border: 1px solid {p.border}; border-radius: {RADIUS // 2}px;
        padding: 4px {PAD_SM}px; color: {p.text};
    }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.muted}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {p.raised}; border: 1px solid {p.border}; selection-background-color: {p.accent}; }}
    QCheckBox {{ spacing: {PAD_SM}px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.raised}; }}
    QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {p.accent}; }}
    QProgressBar {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 5px; text-align: center; min-height: 8px; max-height: 10px; color: transparent; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}

    /* --- tables & lists --- */
    QTableView, QTableWidget, QListWidget, QTreeView {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS // 2}px;
        gridline-color: {p.border}; alternate-background-color: {p.raised};
    }}
    QHeaderView::section {{
        background: {p.raised}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
        padding: 6px {PAD_SM}px; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase;
    }}
    QTableView::item:selected, QListWidget::item:selected {{ background: {p.accent}; color: {p.bg}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 5px; min-width: 24px; }}
    QScrollArea {{ border: none; }}
    QSplitter::handle {{ background: {p.border}; }}
    QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: {RADIUS // 2}px; }}
    QTabBar::tab {{ background: transparent; color: {p.muted}; padding: 6px {PAD_MD}px; }}
    QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
    QMenu {{ background: {p.raised}; border: 1px solid {p.border}; }}
    QMenu::item:selected {{ background: {p.accent}; color: {p.bg}; }}
    QStatusBar {{ background: {p.surface}; color: {p.muted}; border-top: 1px solid {p.border}; }}
    QStatusBar QLabel[warn="true"] {{ color: {p.warn}; font-weight: 600; }}
    QWizard QLabel#WizardTitle {{ font-size: {FONT_SIZES["xl"]}px; font-weight: 600; }}
    QGraphicsView {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: {RADIUS // 2}px; }}

    /* --- mini HUD --- */
    #MiniHud {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS}px; }}
    #MiniHud QLabel {{ background: transparent; }}
    #HudMode {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase; }}
    #HudGems {{ font-size: {FONT_SIZES["lg"]}px; font-weight: 600; }}
    {_accent_rules(p)}
    """
