"""Click the visible Echo inventory and read its details."""

from dataclasses import dataclass
import json
from math import ceil
from pathlib import Path
import re
import sys
from time import monotonic, sleep
from uuid import uuid4

if __name__ == "__main__" and sys.flags.isolated:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_paths import LOCAL_DATA
from ingest import Reader, ReadResult
from scanner import SonataMismatch, read_lines, sonata_names
from win_automation import NativeGame, Process, launch_elevated


COLUMNS = (166, 284, 402, 520, 637, 755)
FIRST_ROW = 151
ROW_STEP = 141
GRID_TOP = 88
GRID_BOTTOM = 607
DETAIL = (873, 74, 1250, 628)
SONATA_FILTER = (108, 554, 817, 665)
SCROLL_TARGET = 104
MAX_SCROLL = 330
BADGE_BOTTOM = GRID_BOTTOM - 61


class ScanStopped(Exception):
    pass


@dataclass(frozen=True)
class Scrollbar:
    x: int
    top: int
    end: int

    @property
    def center(self):
        return (self.top + self.end - 1) // 2

    @property
    def height(self):
        return self.end - self.top


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for attempt in range(3):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 2:
                raise
            sleep(.03)


class ScanJob:
    def __init__(self, root, job_id):
        if not re.fullmatch("[a-f0-9]{32}", job_id):
            raise ValueError("无效的扫描任务编号")
        self.id = job_id
        self.root = Path(root)
        self.folder = self.root / "auto-scan" / job_id
        self.state = self.folder / "state.json"
        self.stop_flag = self.folder / "stop"
        self.anchor = self.folder / "start.json"

    @classmethod
    def latest(cls, root):
        path = Path(root) / "auto-scan" / "recent"
        if path.is_file():
            return cls(root, path.read_text(encoding="utf-8").strip())
        return None

    @classmethod
    def create(cls, root):
        job = cls(root, uuid4().hex)
        job.folder.mkdir(parents=True)
        (job.folder.parent / "recent").write_text(job.id, encoding="utf-8")
        return job

    def read(self):
        if self.state.is_file():
            return json.loads(self.state.read_text(encoding="utf-8"))
        return {"phase": "waiting", "count": 0, "next": 0, "message": "等待游戏窗口"}

    def prepare(self):
        self.stop_flag.unlink(missing_ok=True)
        self.write("waiting", "请在授权后切回鸣潮并停在声骸背包顶部")

    def start(self, script, parent_pid):
        return launch_elevated(script, ["--worker", self.id, str(parent_pid)])

    def write(self, phase, message, *, count=None, next_slot=None, failed=None,
              unreadable_badges=None, sonata_filter=None):
        state = self.read()
        state.update(phase=phase, message=message)
        if count is not None:
            state["count"] = count
        if next_slot is not None:
            state["next"] = next_slot
        if failed is not None:
            state["failed"] = failed
        if unreadable_badges is not None:
            state["unreadable_badges"] = unreadable_badges
        if sonata_filter is not None:
            state["sonata_filter"] = sonata_filter
        write_json(self.state, state)


def gray(frame):
    import numpy as np

    return np.asarray(frame.convert("L"), dtype="float32")


def hsv_section(frame, box):
    import numpy as np

    return np.asarray(frame.crop(box).convert("HSV"))


def is_echo_screen(frame):
    red, saturation, value = hsv_section(frame, (787, 647, 821, 675)).transpose(2, 0, 1)
    red_button = (((red < 18) | (red > 235)) & (saturation > 90) & (value > 135)).sum()
    cost_label = gray(frame.crop((886, 160, 979, 192)))
    return red_button >= 35 and (cost_label > 195).sum() >= 38


def scrollbar(frame):
    import numpy as np

    pixels = gray(frame)
    bar_left = 818
    bar = pixels[GRID_TOP:GRID_BOTTOM, bar_left:828]
    left = pixels[GRID_TOP:GRID_BOTTOM, 814:818].mean(axis=1)
    right = pixels[GRID_TOP:GRID_BOTTOM, 829:834].mean(axis=1)
    bright = bar > (np.maximum(left, right) + 28)[:, None]
    line = bright.sum(axis=1) >= 2
    starts, ends = [], []
    for i, (before, after) in enumerate(zip([False, *line], [*line, False])):
        if after and not before:
            starts.append(i)
        if before and not after:
            ends.append(i)
    candidates = [(b - a, a, b) for a, b in zip(starts, ends) if b - a >= 3]
    if not candidates:
        return None
    _, start, end = max(candidates)
    columns = np.flatnonzero(bright[start:end].mean(axis=0) >= .60)
    if len(columns) < 2:
        return None
    x = int(round(columns.mean())) + bar_left
    return Scrollbar(x, start + GRID_TOP, end + GRID_TOP)


def card_selected(frame, x, y):
    def gold(x0, x1):
        hue, saturation, value = hsv_section(
            frame, (x0, max(y - 49, GRID_TOP), x1, min(y + 49, GRID_BOTTOM))
        ).transpose(2, 0, 1)
        return ((hue >= 21) & (hue <= 54) & (saturation > 39) & (value > 147)).mean()

    return min(gold(x - 55, x - 43), gold(x + 43, x + 55)) > .075


def card_present(frame, x, y):
    import numpy as np

    art = gray(frame.crop((x - 38, y - 33, x + 39, min(y + 32, GRID_BOTTOM))))
    return np.square(np.diff(art, axis=1)).mean() + np.square(np.diff(art, axis=0)).mean() > 180


def badge_text(frame, x, y):
    # A partly filled last row can shift its estimated center by several pixels.
    return frame.crop((x + 5, y + 27, x + 49, min(y + 65, GRID_BOTTOM)))


def badge_labels(frame, y, columns, recognize=read_lines):
    from PIL import Image, ImageOps

    line_height = 108
    tiles = [badge_text(frame, x, y) for x in columns]
    mosaic = Image.new("RGB", (192, line_height * len(tiles)), (8, 12, 18))
    for index, tile in enumerate(tiles):
        mosaic.paste(tile.resize((176, 96), Image.Resampling.BICUBIC), (7, index * line_height + 6))

    def read_values(lines):
        values = [line.text for line in lines if line.confidence >= .82
                  and re.fullmatch(r"(?:\+\d{1,2}|\d{1,4})", line.text)]
        with_level = {value for value in values if value.startswith("+")}
        return with_level or set(values[-1:])

    batches = [[] for _ in tiles]
    for line in recognize(mosaic):
        index = int(line.y // line_height)
        if 0 <= index < len(tiles):
            batches[index].append(line)
    results = {}
    for x, tile, lines in zip(columns, tiles, batches):
        candidates = read_values(lines)
        if not candidates or len(candidates) > 1 or candidates == {"25"}:
            bright = ImageOps.autocontrast(tile.convert("L"), cutoff=1)
            fallback = bright.resize((tile.width * 5, tile.height * 5), Image.Resampling.BICUBIC)
            recognized = read_values(recognize(fallback))
            if len(recognized) == 1:
                candidates = recognized
        results[x] = candidates.pop() if len(candidates) == 1 else None
    return results


def has_stack_mark(frame, x, y):
    _, saturation, value = hsv_section(
        frame, (x - 47, y + 28, x - 20, min(y + 65, GRID_BOTTOM))
    ).transpose(2, 0, 1)
    white = (value > 161) & (saturation < 110)
    for center in range(8, 19):
        for index, line in enumerate(white):
            if line[center - 5:center + 6].sum() < 9:
                continue
            gaps = [offset for offset, lower in enumerate(white[index + 3:index + 12], 3)
                    if lower[center - 6:center - 2].any() and lower[center + 3:center + 7].any()
                    and not lower[center - 1:center + 2].any()]
            if gaps and gaps[-1] - gaps[0] >= 3:
                return True
    return False


def has_owner_portrait(frame, x, y):
    hue, saturation, value = hsv_section(
        frame, (x - 52, y - 70, x - 14, y - 20)
    ).transpose(2, 0, 1)
    bright = (value > 173) & ((saturation < 115) | ((hue > 19) & (hue < 54)))
    for left in range(3, 12):
        for top in range(3, 23):
            for width in (19, 21, 23, 24):
                right, bottom = left + width, top + width
                horizontal = slice(left + 4, right - 4)
                vertical = slice(top + 4, bottom - 4)
                if (bright[top:top + 2, horizontal].any(axis=0).mean() >= .74
                    and bright[bottom - 2:bottom, horizontal].any(axis=0).mean() >= .74
                    and bright[vertical, left:left + 2].any(axis=1).mean() >= .74
                    and bright[vertical, right - 2:right].any(axis=1).mean() >= .74):
                    return True
    return False


def level_boundary(frame, x, y, label):
    if label == "+25":
        return ""
    if has_stack_mark(frame, x, y):
        return "堆叠声骸"
    if label and re.fullmatch(r"\+(?:[0-9]|1[0-9]|2[0-4])", label):
        # A second, higher crop avoids stopping on a clipped or misread digit.
        if badge_labels(frame, y - 4, (x,))[x] == label:
            return f"{label} 声骸"
    return ""


def row_center(frame, expected):
    import numpy as np

    pixels = gray(frame)
    signal = np.median(
        [np.abs(np.diff(pixels[:, x - 41:x + 42], axis=1)).mean(axis=1) for x in COLUMNS],
        axis=0,
    )
    edge = expected - 50
    candidates = [
        (signal[y + 4:y + 17].mean() - signal[y - 15:y - 4].mean(), y)
        for y in range(max(GRID_TOP, edge - 10), min(GRID_BOTTOM - 17, edge + 11))
    ]
    gradient, best = max(candidates, default=(0, edge))
    return best + 50 if gradient >= 3 else expected


def stats_signature(frame):
    return gray(frame.crop((885, 302, 1241, 533))) > 206


def filtered_sonata(frame, recognize=read_lines):
    from PIL import Image

    label = frame.crop(SONATA_FILTER)
    scaled = label.resize((label.width * 2, label.height * 2), Image.Resampling.BICUBIC)
    names = sonata_names(recognize(scaled))
    return next(iter(names)) if len(names) == 1 else ""


def anchor_signature(image):
    import numpy as np

    levels = gray(image.resize((65, 16)))
    bits = np.diff(levels, axis=1).ravel() > 0
    return np.packbits(bits).tobytes().hex()


def migrate_anchors(root):
    from PIL import Image

    for legacy in (root / "auto-scan").glob("*/start.png"):
        if not re.fullmatch("[a-f0-9]{32}", legacy.parent.name):
            continue
        destination = legacy.with_suffix(".json")
        if not destination.exists():
            with Image.open(legacy) as image:
                write_json(destination, anchor_signature(image))
        legacy.unlink()


def scroll_distance(previous, current, *, expected=None, tolerance=0, moved=False):
    import cv2
    import numpy as np

    def strip(frame):
        return np.diff(gray(frame.crop((121, GRID_TOP, 802, GRID_BOTTOM - 25)))[:, ::2], axis=1)

    before, after = strip(previous), strip(current)
    patch_height = 82
    search_end = MAX_SCROLL + 35
    totals = np.zeros(search_end + 1)
    samples = np.zeros_like(totals)
    support = np.zeros_like(totals)
    for start in (10, 62, 124, 186, 248, 310):
        needle = np.ascontiguousarray(after[start:start + patch_height])
        if needle.std() < 3:
            continue
        matches = cv2.matchTemplate(np.ascontiguousarray(before[start:]), needle,
                                    cv2.TM_CCOEFF_NORMED).ravel()[:search_end + 1]
        totals[:len(matches)] += matches
        samples[:len(matches)] += 1
        support[:len(matches)] += matches >= .57
    scores = np.divide(totals, samples, out=np.zeros_like(totals), where=samples > 0)
    shifts = np.arange(len(scores))
    viable = (support >= 2) & (scores >= .57)
    if moved:
        viable[:4] = False
    if expected is not None:
        viable &= np.abs(shifts - expected) <= tolerance
    if viable.any():
        highest = int(np.argmax(np.where(viable, scores, -1)))
        rivals = viable & (np.abs(shifts - highest) > 8)
        margin = scores[highest] - scores[rivals].max() if rivals.any() else scores[highest]
        if margin >= .043:
            return highest
    raise ScanStopped("无法可靠定位拖动前后的重叠行")


def same_viewport(previous, current):
    import numpy as np

    before = gray(previous.crop((121, GRID_TOP, 802, GRID_BOTTOM - 25)))[:, ::3]
    after = gray(current.crop((121, GRID_TOP, 802, GRID_BOTTOM - 25)))[:, ::3]
    return np.abs(np.diff(before, axis=1) - np.diff(after, axis=1)).mean() < 1.8


def advance_inventory(game, before, pixels_per_drag, take_frame, wait):
    origin = scrollbar(before)
    if origin is None:
        raise ScanStopped("未识别到声骸列表右侧的滚动条")
    thumb = origin
    scale = pixels_per_drag or (GRID_BOTTOM - GRID_TOP) / origin.height
    limit = max(1, GRID_BOTTOM - origin.end + 6)
    step = max(1, min(round(SCROLL_TARGET / scale), limit))
    tried = set()
    thumb_travel = 0
    for _ in range(7):
        tried.add(step)
        target = min(GRID_BOTTOM + 6, origin.center + step)
        game.drag_scrollbar(thumb.x, thumb.center, target)
        game.point(842, 658)
        wait(.30)
        frame = take_frame()
        for _ in range(5):
            wait(.13)
            later = take_frame()
            settled = same_viewport(frame, later)
            frame = later
            if settled:
                break
        else:
            raise ScanStopped("列表滚动后未稳定，已保留已入库的信息")
        thumb = scrollbar(frame)
        if thumb is None:
            wait(.22)
            frame = take_frame()
            thumb = scrollbar(frame)
            if thumb is None:
                raise ScanStopped("拖动后滚动条不可见，已保留已入库的信息")

        thumb_travel = thumb.center - origin.center
        if thumb_travel < -1:
            raise ScanStopped("滚动条向上移动，无法确认扫描位置")
        try:
            expected = pixels_per_drag * thumb_travel if pixels_per_drag and thumb_travel > 0 else None
            tolerance = min(62, max(17, pixels_per_drag * .65)) if expected else 0
            movement = (
                0 if thumb_travel == 0 and same_viewport(before, frame)
                else scroll_distance(before, frame, expected=expected, tolerance=tolerance,
                                     moved=thumb_travel > 0)
            )
        except ScanStopped:
            movement = None
        if movement is not None and 3 < movement <= MAX_SCROLL:
            measured = movement / thumb_travel if thumb_travel > 0 else pixels_per_drag
            if measured and pixels_per_drag:
                measured = (measured + pixels_per_drag) / 2
            return frame, movement, measured

        if movement is not None and movement <= 3 and abs(thumb_travel) <= 1:
            bottom = origin.end >= GRID_BOTTOM - 6 and thumb.end >= GRID_BOTTOM - 6
            if bottom and target >= origin.center + max(3, GRID_BOTTOM - origin.end + 1):
                return frame, 0, pixels_per_drag
            candidates = (min(limit, step * 2), min(limit, step + 1), 1)
        else:
            candidates = (max(1, step // 2), max(1, step - 1), min(limit, step + 1))
        step = next((candidate for candidate in candidates if candidate not in tried), None)
        if step is None:
            break
    raise ScanStopped(
        f"拖动 {len(tried)} 次后仍无法确认列表重叠（滑块位移 {thumb_travel} 像素），"
        "已保留已入库的信息"
    )


def scan(game, job, stop, record, pause=sleep, *, record_failure=None):
    def check():
        if stop():
            raise ScanStopped("已停止自动扫描")

    def wait(seconds):
        intervals = ceil(seconds / .025)
        for _ in range(intervals):
            pause(seconds / intervals)
            check()

    def take_frame():
        check()
        frame = game.frame()
        if not is_echo_screen(frame):
            raise ScanStopped("请停在声骸背包列表并打开右侧详情")
        return frame

    frame = take_frame()
    thumb = scrollbar(frame)
    if not thumb or thumb.top > GRID_TOP + 15:
        raise ScanStopped("请先把声骸背包列表滚到顶部，再启动或继续扫描")
    migrate_anchors(job.root)
    start = anchor_signature(frame.crop((119, 103, 802, 202)))
    if job.anchor.exists():
        previous = json.loads(job.anchor.read_text(encoding="utf-8"))
        if (int(previous, 16) ^ int(start, 16)).bit_count() > 110:
            raise ScanStopped("背包首行已变动，请恢复原排序或选择从头扫描")
    else:
        write_json(job.anchor, start)
    state = job.read()
    next_slot, count = state["next"], state["count"]
    sonata = filtered_sonata(frame)
    if next_slot and state.get("sonata_filter") and state["sonata_filter"] != sonata:
        raise ScanStopped("合鸣筛选与上次扫描不一致或未读清，请恢复原筛选后继续")
    job.write("running", "正在扫描" + (f"「{sonata}」" if sonata else "声骸背包"),
              sonata_filter=sonata)
    failed = state.get("failed", 0)
    unreadable = state.get("unreadable_badges", 0)
    y, row = FIRST_ROW, 0
    pixels_per_drag = None

    def checkpoint(slot):
        prefix = f"「{sonata}」 · " if sonata else ""
        job.write("running", f"{prefix}已处理 +25 声骸 {count} 件 · Esc 停止",
                  count=count, next_slot=slot + 1, failed=failed,
                  unreadable_badges=unreadable)

    while True:
        if y <= BADGE_BOTTOM:
            if y < GRID_TOP + 22:
                raise ScanStopped("滚动跨度过大，无法确认中间行")
            y = row_center(frame, y)
            if y > BADGE_BOTTOM:
                continue
            pending = [(column, x) for column, x in enumerate(COLUMNS)
                       if row * len(COLUMNS) + column >= next_slot]
            visible = [x for _, x in pending if card_present(frame, x, y)]
            labels = badge_labels(frame, y, visible) if visible else {}
            for column, x in pending:
                slot = row * len(COLUMNS) + column
                check()
                if x not in labels:
                    thumb = scrollbar(frame)
                    if thumb and thumb.end >= GRID_BOTTOM - 6:
                        break
                    raise ScanStopped("无法确认下一张卡片的位置，已保留已入库的信息")
                label = labels[x]
                boundary = level_boundary(frame, x, y, label)
                if boundary:
                    return count, f"遇到首张{boundary}，已结束扫描"
                record_id = f"capture-auto-{job.id}-{slot:06d}"
                if label != "+25":
                    if record_failure:
                        record_failure(record_id, "等级未读清，已跳过这张卡片")
                    unreadable += 1
                    next_slot = slot + 1
                    checkpoint(slot)
                    continue
                equipped_hint = has_owner_portrait(frame, x, y)
                previous_detail = stats_signature(frame)
                game.click(x, y)
                wait(.20)
                game.point(842, 658)
                signature = None
                for attempt in range(9):
                    frame = take_frame()
                    if card_selected(frame, x, y):
                        now = stats_signature(frame)
                        changed = (now != previous_detail).mean() > .002
                        stable = signature is not None and (signature != now).mean() < .006
                        if stable and (changed or attempt >= 4):
                            break
                        signature = now
                    else:
                        signature = None
                    wait(.09)
                else:
                    raise ScanStopped("点选后无法确认卡片高亮或详情稳定，已停止")
                result = record(frame.crop(DETAIL), record_id,
                                equipped_hint=equipped_hint, sonata_hint=sonata)
                if result is ReadResult.FAILED:
                    failed += 1
                if result is ReadResult.SKIPPED:
                    return count, "详情显示低于 +25，已结束扫描"
                count += 1
                next_slot = slot + 1
                checkpoint(slot)
            else:
                row += 1
                y += ROW_STEP
                continue

        check()
        frame, movement, pixels_per_drag = advance_inventory(
            game, frame, pixels_per_drag, take_frame, wait
        )
        if not movement:
            return count, "已到背包底部"
        y -= movement


def worker(job_id, parent_pid):
    import ctypes
    from ctypes import wintypes

    job = ScanJob(LOCAL_DATA, job_id)
    if not job.folder.is_dir():
        raise ValueError("自动扫描任务不存在")
    mutex_api = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex_api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    mutex_api.CreateMutexW.restype = wintypes.HANDLE
    mutex_api.CloseHandle.argtypes = (wintypes.HANDLE,)
    mutex = mutex_api.CreateMutexW(None, False, r"Local\MingchaoHelpEchoScan")
    if not mutex or ctypes.get_last_error() == 183:
        if mutex:
            mutex_api.CloseHandle(mutex)
        job.write("stopped", "已有自动扫描进程在运行")
        return
    parent = None
    try:
        parent = Process.open(parent_pid)
        game = NativeGame()

        def stop():
            return job.stop_flag.exists() or not parent.running() or game.stopped()

        deadline = monotonic() + 35
        while not game.foreground_matches():
            if stop():
                raise ScanStopped("已停止自动扫描")
            if monotonic() >= deadline:
                raise ScanStopped("等待鸣潮窗口超时")
            sleep(.12)
        for _ in range(8):
            if stop() or not game.foreground_matches():
                raise ScanStopped("鸣潮已离开前台或扫描被取消")
            sleep(.10)
        job.write("running", "正在识别声骸背包")
        with Reader(job.root) as reader:
            count, reason = scan(game, job, stop, reader.record, record_failure=reader.db.record_failure)
        job.write("complete", f"{reason} · 共处理 +25 声骸 {count} 件")
    except (ScanStopped, SonataMismatch, OSError, ValueError) as error:
        job.write("stopped", str(error))
    except Exception as error:
        job.write("stopped", f"扫描进程出错：{type(error).__name__} {error}")
    finally:
        if parent:
            parent.close()
        mutex_api.CloseHandle(mutex)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("请从 MingchaoHelp 启动自动扫描")
    worker(sys.argv[2], int(sys.argv[3]))
