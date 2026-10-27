"""Who has control of which browser: requested, held, handed back.

A person clicking Control while an agent's call runs *requests* it, and the call
finishes first; Control is *granted* once the calls that asked before the
request have had their turn on the session's lock, and once the pages the
person reaches are followed (spec 2026-10-27-live-control, rulings 1, 13, 14,
25 and 27). Every request is answered ``requested`` first, even with no call
running: the grant waits on the monitor, so the viewer hears ``control`` only
once the pages are followed or could not be (ruling 19). From the request on,
the browser is controlled: a call that would change it is refused with
`faults.Controlled`, a 409, naming who and since when, and reads keep
answering. The last viewer to leave Control, or to withdraw a request, hands
the browser back (rulings 7 and 23).

One episode per workspace, run on the event loop. The one thing read off it is
the gate: a dict from Grid id to a frozen `Gate`, replaced whole and never
mutated, so a worker thread's `refusal` is one dict read (as the monitor's
``watches`` are read). The grant's turn is a mark in the lock's queue that the
loop polls; no thread ever waits on the lock for it (ruling 25).

Process memory, like the tunnels: a restart forgets every episode, and with
them every tunnel, which is the truth.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from ..core.capabilities import UNDER_CONTROL
from ..faults import Controlled

PERSON = "a person"
ANONYMOUS = ("admin", "open")
# The only caller of a `paste` is the admin's paste route, after its own
# `controlling` check: the person in control is never refused (ruling 3).
PASTE = "paste"

HELD = (
    "{who} has control of this browser since {at} UTC. Call await_control "
    "(POST /browser/await-control), which returns when they hand back."
)
REQUESTED = (
    "{who} asked for control of this browser at {at} UTC. Call await_control "
    "(POST /browser/await-control), which returns when they hand back."
)
NOTICE_REQUESTED = (
    "{who} asked for control of this browser at {at} UTC; it is theirs once this "
    "call ends. Call await_control, which returns when they hand back."
)
HANDED_BACK = (
    "control of this browser was handed back at {at} UTC; the page may have "
    "changed. Call await_control for what they did, or look at the page before "
    "carrying on."
)

ANSWERS = frozenset(name for name, says in UNDER_CONTROL.items() if says == "answers")


def names(who: Iterable[str]) -> list[str]:
    """Who, as a sentence names them: `admin` and `open` are "a person", once."""
    return list(dict.fromkeys(PERSON if name in ANONYMOUS else name for name in who))


def said(who: Iterable[str]) -> str:
    return " and ".join(names(who))


def _hhmm(at: float) -> str:
    return time.strftime("%H:%M", time.gmtime(at))


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


@dataclass(frozen=True)
class Gate:
    """What a worker thread reads: replaced whole, never mutated."""

    workspace: str
    who: tuple[str, ...]  # the viewers asking or holding now, in order
    since: float  # when this state began: the request, or the grant
    state: Literal["requested", "held"]


@dataclass(eq=False)
class Episode:
    workspace: str
    grid_id: str  # never logged
    who: list[str]  # everyone who asked during the episode, in order, once
    since: float  # requested
    state: Literal["requested", "held", "released"]
    granted: float | None = None
    until: float | None = None
    ended: bool = False  # the browser ended under it
    followed: bool = False  # the grant's attach held: the pages were listened for
    # The pages the person reached, oldest first, as `Pages.listed` gives them
    # (``{"url", "title"?}``, URLs as heard), and how many earlier ones went;
    # taken at the release.
    pages: list[dict] = field(default_factory=list, repr=False)
    dropped: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    # The tunnels asking or holding now, and the grant's poll while requested.
    tunnels: list = field(default_factory=list, repr=False)
    pending: asyncio.Task | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Waited:
    kind: Literal["handed_back", "still", "nobody"]
    episode: Episode | None


def _running_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # no loop running: nothing of ours is either
        return None


class Controls:
    """The episodes, and the gate every host reads before it drives a browser.

    ``behind(grid_id)`` marks the session's lock queue and returns whether the
    calls before the mark have had their turn (`workspace.locks.behind`).
    ``monitor`` (`Monitor.attach_here` and `release`) and ``pages``
    (`live.pages.Pages`) follow the pages the person reaches; without them the
    grant lands unfollowed.
    """

    def __init__(
        self,
        *,
        behind: Callable[[str], Callable[[], bool]],
        monitor=None,
        pages=None,
        clock: Callable[[], float] = time.time,
        poll: float = 0.1,
    ):
        self._behind = behind
        self._monitor = monitor
        self._pages = pages
        self._clock = clock
        self._poll = poll
        self._episodes: dict[str, Episode] = {}  # workspace -> requested or held
        self._unsaid: dict[str, Episode] = {}  # workspace -> its last hand-back
        self._gates: dict[str, Gate] = {}  # Grid id -> read from any thread

    # ---- the person's side, on the loop ---------------------------------------

    def ask(self, tunnel) -> Literal["control", "requested"]:
        """Start or join the workspace's episode; what the viewer is told now."""
        episode = self._episodes.get(tunnel.workspace)
        if episode is not None and episode.grid_id != tunnel.grid_id:
            # The workspace holds another browser now: the old one ended.
            self._release(episode, ended=True)
            episode = None
        if episode is None:
            # A new episode makes an unsaid hand-back moot (ruling 17: one kept).
            self._unsaid.pop(tunnel.workspace, None)
            episode = Episode(
                tunnel.workspace,
                tunnel.grid_id,
                who=[tunnel.who],
                since=self._clock(),
                state="requested",
            )
            episode.tunnels.append(tunnel)
            self._episodes[tunnel.workspace] = episode
            self._shut(episode)
            # The grant is a task even when no call is running: it waits for
            # the pages to be followed, and the viewer hears "requested" first.
            episode.pending = asyncio.get_running_loop().create_task(
                self._turn(episode, self._behind(tunnel.grid_id))
            )
            return "requested"
        if tunnel not in episode.tunnels:
            episode.tunnels.append(tunnel)
            if tunnel.who not in episode.who:
                episode.who.append(tunnel.who)
            self._shut(episode)
        return "control" if episode.state == "held" else "requested"

    def withdraw(self, tunnel) -> None:
        """The viewer went to Watch, or its tunnel ended. The last one hands back."""
        episode = self._episodes.get(tunnel.workspace)
        if episode is None or tunnel not in episode.tunnels:
            return
        episode.tunnels.remove(tunnel)
        if episode.tunnels:
            self._shut(episode)
        else:
            self._release(episode)

    def ended(self, grid_id: str) -> None:
        """`session.ended`: the browser went from under the episode."""
        for episode in list(self._episodes.values()):
            if episode.grid_id == grid_id:
                self._release(episode, ended=True)

    def stop(self) -> None:
        """The server is stopping: every pending grant goes, every waiter returns
        with its episode, and nothing is left to be said to a later call."""
        for episode in list(self._episodes.values()):
            self._release(episode)
        self._unsaid.clear()

    # ---- the grant -------------------------------------------------------------

    async def _turn(self, episode: Episode, passed: Callable[[], bool]) -> None:
        """Poll the mark from the loop, then grant; cancelled by the release."""
        try:
            while not passed():
                await asyncio.sleep(self._poll)
            await self._grant(episode)
        finally:
            if episode.pending is _running_task():
                episode.pending = None

    async def _grant(self, episode: Episode) -> None:
        """Follow the pages, then hold and tell every requesting viewer. A page
        the person reaches must not be missed, so the grant waits for the
        attach, up to ``ATTACH_TIMEOUT`` (ruling 27); one that fails grants
        all the same, unfollowed (ruling 19)."""
        if episode.state != "requested":
            return
        episode.followed = await self._follow(episode)
        if episode.state != "requested":  # released meanwhile
            return
        self._hold(episode)
        for tunnel in list(episode.tunnels):
            if episode.state != "held":  # released while an earlier one was told
                return
            try:
                await tunnel.grant()
            except Exception:  # noqa: BLE001 - one viewer's failure is its own
                # A `Tunnel` ends itself on a failure it did not expect; nothing
                # here is the next viewer's business.
                continue

    async def _follow(self, episode: Episode) -> bool:
        """Owe the monitor ``control`` for this browser, its pages listed from
        now; whether the connection is held. Cancelled by the release."""
        if self._monitor is None or self._pages is None:
            return False
        self._pages.follow(episode.grid_id)
        try:
            await self._monitor.attach_here(
                episode.workspace, episode.grid_id, "control"
            )
        except Exception:  # noqa: BLE001 - BidiUnavailable: granted unfollowed
            return False
        return True

    def _hold(self, episode: Episode) -> None:
        episode.state = "held"
        episode.granted = self._clock()
        self._shut(episode)

    # ---- the release -------------------------------------------------------------

    def _release(self, episode: Episode, *, ended: bool = False) -> None:
        if episode.state == "released":
            return
        pending, episode.pending = episode.pending, None
        if pending is not None and pending is not _running_task():
            pending.cancel()
        episode.state = "released"
        episode.until = self._clock()
        episode.ended = ended
        episode.tunnels.clear()
        # Never awaits: called from withdraw, ended and stop. An attach still
        # under way went with the pending grant, cancelled above.
        if self._monitor is not None:
            self._monitor.release(episode.grid_id, "control")
        if self._pages is not None:
            episode.pages, episode.dropped = self._pages.listed(episode.grid_id)
            self._pages.stop(episode.grid_id)
        if self._episodes.get(episode.workspace) is episode:
            del self._episodes[episode.workspace]
        gate = self._gates.get(episode.grid_id)
        if gate is not None and gate.workspace == episode.workspace:
            self._gates.pop(episode.grid_id, None)
        self._unsaid[episode.workspace] = episode
        episode.done.set()

    def _shut(self, episode: Episode) -> None:
        """Replace the episode's gate whole with what it is now."""
        state = "held" if episode.state == "held" else "requested"
        since = episode.granted if state == "held" else episode.since
        who = tuple(dict.fromkeys(t.who for t in episode.tunnels))
        self._gates[episode.grid_id] = Gate(episode.workspace, who, since, state)

    # ---- what the callers hear ----------------------------------------------------

    def refusal(self, session_id: str, tool: str) -> Controlled | None:
        """The refusal for ``tool`` on this browser, or None. From any thread."""
        gate = self._gates.get(session_id)
        if gate is None or tool == PASTE or tool in ANSWERS:
            return None
        sentence = HELD if gate.state == "held" else REQUESTED
        return Controlled(sentence.format(who=said(gate.who), at=_hhmm(gate.since)))

    def controlling(self, workspace: str, who: str) -> bool:
        """Whether ``who`` holds Control of the workspace now: never while requested."""
        episode = self._episodes.get(workspace)
        return (
            episode is not None
            and episode.state == "held"
            and any(t.who == who for t in episode.tunnels)
        )

    def notice(self, workspace: str) -> dict | None:
        """What the agent's result says about control, or None; a hand-back once."""
        episode = self._episodes.get(workspace)
        if episode is not None:
            gate = self._gates.get(episode.grid_id)
            if gate is not None:
                sentence = HELD if gate.state == "held" else NOTICE_REQUESTED
                return {
                    "state": gate.state,
                    "who": names(gate.who),
                    "since": _iso(gate.since),
                    "hint": sentence.format(who=said(gate.who), at=_hhmm(gate.since)),
                }
        back = self._unsaid.pop(workspace, None)
        if back is None:
            return None
        return {
            "state": "handed_back",
            "who": names(back.who),
            "since": _iso(back.until),
            "hint": HANDED_BACK.format(at=_hhmm(back.until)),
        }

    async def wait(self, workspace: str, timeout: float | None) -> Waited:
        """Until the workspace's episode ends, up to ``timeout`` (None: no cap).

        With none under way, at once: the unsaid hand-back, which this says, or
        nobody. Cancelled, it changes nothing: the episode and its event are
        the person's, not the waiter's.
        """
        episode = self._episodes.get(workspace)
        if episode is None:
            back = self._unsaid.pop(workspace, None)
            if back is None:
                return Waited("nobody", None)
            return Waited("handed_back", back)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                await episode.done.wait()
        if not episode.done.is_set():
            return Waited("still", episode)
        if self._unsaid.get(workspace) is episode:
            del self._unsaid[workspace]
        return Waited("handed_back", episode)
