from padforge.wmdrag import SuperDrag

from conftest import Hand

THREE = [(40, 30), (55, 28), (70, 30)]


def run(sd, frames, held=True):
    out = []
    for f in frames:
        out += sd.feed(f, lambda: held)
    return out


def kinds(ev):
    return [(e.kind, e.button) for e in ev if e.kind != "move"]


def test_super_one_finger_moves_window():
    h, sd = Hand(), SuperDrag()
    ev = run(sd, h.put((50, 30)) + h.move(20, 5, 0.2) + h.lift())
    assert kinds(ev) == [("press", 1), ("release", 1)]


def test_super_three_fingers_resize_and_move_pointer():
    h, sd = Hand(), SuperDrag(resize_button=2)
    ev = run(sd, h.put(*THREE) + h.move(15, 10, 0.2) + h.lift())
    assert kinds(ev) == [("press", 2), ("release", 2)]
    moves = [e for e in ev if e.kind == "move"]
    assert abs(sum(e.dx for e in moves) - 15) < 0.5 and abs(sum(e.dy for e in moves) - 10) < 0.5


def test_staggered_three_fingers_do_not_start_a_move_first():
    """Fingers land ~20 ms apart: wait before deciding it is a one-finger move."""
    h, sd = Hand(), SuperDrag()
    frames = h.put(THREE[0]) + h.put(THREE[1]) + h.put(THREE[2]) + h.move(10, 0, 0.1) + h.lift()
    assert kinds(run(sd, frames)) == [("press", 2), ("release", 2)]


def test_without_super_nothing_happens():
    h, sd = Hand(), SuperDrag()
    ev = run(sd, h.put((50, 30)) + h.move(20, 5, 0.2) + h.lift(), held=False)
    assert ev == [] and not sd.active


def test_two_fingers_with_super_are_left_alone():
    h, sd = Hand(), SuperDrag()
    ev = run(sd, h.put((40, 30), (60, 30)) + h.move(0, 20, 0.3) + h.lift())
    assert ev == []


def test_super_checked_once_per_touch():
    h, sd = Hand(), SuperDrag()
    calls = []
    for f in h.put((50, 30)) + h.move(10, 0, 0.1) + h.lift():
        sd.feed(f, lambda: calls.append(1) or True)
    assert len(calls) == 1


def test_cancel_releases():
    h, sd = Hand(), SuperDrag()
    run(sd, h.put((50, 30)) + h.move(10, 0, 0.1))
    assert [(e.kind, e.button) for e in sd.cancel()] == [("release", 1)]


def test_disabled_parts():
    h, sd = Hand(), SuperDrag(move=False, resize=True)
    assert run(sd, h.put((50, 30)) + h.move(20, 0, 0.2) + h.lift()) == []
