"""Jamaa Night design system: tokens in one place, QSS generated from them (KICKOFF §12.1).

No widget sets an inline color. Everything reads ``PALETTE`` or uses an object name / property
that the generated stylesheet targets.
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
        }.get(mode, self.accent)

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

FONT_FAMILY = "Segoe UI Variable"
FONT_FALLBACK = "Segoe UI"
FONT_SIZES = {"xs": 12, "sm": 13, "md": 15, "lg": 18, "xl": 24, "xxl": 32}
RADIUS = 12
GRID = 4
PAD_SM = 8
PAD_MD = 16
PAD_LG = 24
NAV_WIDTH = 200
NAV_COLLAPSED = 64
COMMAND_BAR_HEIGHT = 64
HUD_SIZE = (320, 120)


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
        background: transparent; color: {p.muted}; border: none; border-radius: {RADIUS // 2}px;
        padding: {PAD_SM}px; text-align: left; font-size: {FONT_SIZES["sm"]}px;
    }}
    #NavRail QToolButton:hover {{ background: {p.raised}; color: {p.text}; }}
    #NavRail QToolButton:checked {{ background: {p.raised}; color: {p.text}; }}
    #NavBrand {{ font-size: {FONT_SIZES["lg"]}px; font-weight: 600; color: {p.text}; padding: {PAD_SM}px; }}

    /* --- command bar --- */
    #CommandBar {{ background: {p.surface}; border-bottom: 1px solid {p.border}; }}
    #StatePill {{
        background: {p.raised}; color: {p.text}; border: 1px solid {p.border};
        border-radius: 18px; padding: 6px {PAD_MD}px; font-size: {FONT_SIZES["md"]}px; font-weight: 600;
        min-height: 24px;
    }}
    #StatePill[state="running"] {{ border-color: {p.ok}; color: {p.ok}; }}
    #StatePill[state="paused"] {{ border-color: {p.warn}; color: {p.warn}; }}
    #StatePill[state="error"] {{ border-color: {p.danger}; color: {p.danger}; }}
    #StatePill[state="idle"] {{ border-color: {p.border}; color: {p.muted}; }}
    #LeaseIndicator, #HotkeyHint {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; }}

    /* --- cards & pages --- */
    #Page {{ background: {p.bg}; }}
    #PageTitle {{ font-size: {FONT_SIZES["xl"]}px; font-weight: 600; padding: 0 0 {PAD_SM}px 0; }}
    #PageSubtitle {{ color: {p.muted}; font-size: {FONT_SIZES["sm"]}px; }}
    QFrame#Card {{
        background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS}px;
    }}
    QLabel, QCheckBox, QRadioButton, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
    #CardTitle {{ font-size: {FONT_SIZES["md"]}px; font-weight: 600; }}
    #CardHelp {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; }}
    #StatValue {{ font-size: {FONT_SIZES["xxl"]}px; font-weight: 600; font-variant-numeric: tabular-nums; }}
    #StatLabel {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase; }}
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
    QPushButton:disabled {{ color: {p.muted}; border-color: {p.surface}; }}
    QPushButton[kind="primary"] {{ background: {p.accent}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="danger"] {{ background: {p.danger}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="ok"] {{ background: {p.ok}; color: {p.bg}; border: none; font-weight: 600; }}
    QPushButton[kind="link"] {{ background: transparent; border: none; color: {p.accent}; padding: 0; text-decoration: underline; }}
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: {p.raised}; border: 1px solid {p.border}; border-radius: {RADIUS // 2}px;
        padding: 4px {PAD_SM}px; color: {p.text};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {p.raised}; border: 1px solid {p.border}; selection-background-color: {p.accent}; }}
    QCheckBox {{ spacing: {PAD_SM}px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.raised}; }}
    QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {p.accent}; }}
    QProgressBar {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 6px; text-align: center; height: 12px; }}
    QProgressBar::chunk {{ background: {p.accent}; border-radius: 6px; }}

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
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
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
    QWizard QLabel#WizardTitle {{ font-size: {FONT_SIZES["xl"]}px; font-weight: 600; }}

    /* --- mini HUD --- */
    #MiniHud {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: {RADIUS}px; }}
    #MiniHud QLabel {{ background: transparent; }}
    #HudMode {{ color: {p.muted}; font-size: {FONT_SIZES["xs"]}px; text-transform: uppercase; }}
    #HudGems {{ font-size: {FONT_SIZES["lg"]}px; font-weight: 600; }}
    """
