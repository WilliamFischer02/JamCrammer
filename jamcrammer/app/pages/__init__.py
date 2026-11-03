"""Page registry (KICKOFF §12.2 nav order)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jamcrammer.app.widgets import Page

if TYPE_CHECKING:
    from jamcrammer.app.engine import Engine
    from jamcrammer.app.main_window import MainWindow


def build_pages(engine: Engine, window: MainWindow) -> list[Page]:
    from jamcrammer.app.pages.adventures import AdventuresPage
    from jamcrammer.app.pages.answerbank import AnswerBankPage
    from jamcrammer.app.pages.calibration import CalibrationPage
    from jamcrammer.app.pages.dashboard import DashboardPage
    from jamcrammer.app.pages.learn import LearnPage
    from jamcrammer.app.pages.logs import LogsPage
    from jamcrammer.app.pages.mapping import MappingPage
    from jamcrammer.app.pages.modes import ArcadePage, BestGuessPage, OpportunistPage
    from jamcrammer.app.pages.notifications import NotificationsPage
    from jamcrammer.app.pages.parties import PartiesPage
    from jamcrammer.app.pages.profitlab import ProfitLabPage
    from jamcrammer.app.pages.settings import SettingsPage

    # The Den Planner page is gone (2026-11-03): its flow is the "Hunt Best Guess" checkbox
    # on every farmer bot's run panel (modes/planner.py BestGuessPlan, engine._run_plan).
    return [
        DashboardPage(engine, window),
        BestGuessPage(engine, window),
        ArcadePage(engine, window),
        AdventuresPage(engine, window),
        OpportunistPage(engine, window),
        MappingPage(engine, window),
        LearnPage(engine, window),
        CalibrationPage(engine, window),
        AnswerBankPage(engine, window),
        PartiesPage(engine, window),
        ProfitLabPage(engine, window),
        NotificationsPage(engine, window),
        SettingsPage(engine, window),
        LogsPage(engine, window),
    ]
