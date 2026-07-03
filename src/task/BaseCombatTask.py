import re
import time

import numpy as np

from ok import Box, Logger
from ok import safe_get
from src import text_white_color
from src.char import BaseChar
from src.char.BaseChar import SwitchPriority, dot_color  # noqa
from src.char.CharFactory import get_char_by_pos
from src.combat.CombatCheck import CombatCheck
from src.task.BaseWWTask import isolate_white_text_to_black, binarize_for_matching

logger = Logger.get_logger(__name__)
cd_regex = re.compile(r'\d{1,2}\.\d')


class NotInCombatException(Exception):
    """未处于战斗状态异常。"""


class CharDeadException(NotInCombatException):
    """角色死亡异常。"""


class CharRevivedException(CharDeadException):
    """角色已复活，用于中断当前战斗上下文并让任务重新进入。"""


mismatched_names = {
    "Douling": "Buling",
    "Xigelika": "Sigrika",
    "Linnai": "Lynae",
    "Luhesi": "Luuk Herssen",
    "Xiangliyao": "Xiangli Yao",
    "ShoreKeeper": "Shorekeeper",
    "HavocRover": "Rover: Havoc"
}

# =============================================================================
# Concerto ring reading -- REWORKED.
#
# The old reader ran two channels and a tower of patches on top: a pixel-AREA
# channel (count_rings: color mask -> connected components -> a contour
# convexity test for "full", with percent = area / a PERSISTED per-element
# baseline) corrected by an angular PRESENCE channel (>=1 matched pixel lights
# a sector). It was wrong most of the time because both channels were built on
# sand:
# - the convexity "full ring" test false-positives on ~97% rings (approxPolyDP
#   erases a small gap) and false-negatives on genuine fulls split into two
#   components by a notch/VFX shadow -- and the multi-component path then
#   ZEROED the area, so a nearly-full ring could read 0.0;
# - the persisted baseline (per ELEMENT, shared across characters, sessions and
#   resolutions) drifted and poisoned every percent derived from it -- the
#   CON_FULL_MAX_RATIO / "healing" / 0.99-cap / arc-rescue patches all existed
#   to fight that one bad idea;
# - presence-based sectors let a single VFX pixel light a bin (fake arcs) while
#   an anti-aliased dim sector with 0 in-range pixels broke a genuine arc.
#
# The rework makes the GEOMETRY the one and only measurement:
# - per-sector DENSITY over the annulus band (fraction of band pixels that are
#   ring-coloured), so speckle cannot light a sector and AA dimming cannot
#   darken one; the ring fills as one contiguous arc, so the longest lit run IS
#   the concerto fraction -- no baseline, nothing to calibrate or poison;
# - a near-white BRIGHT mask unioned into sector density (inside the band
#   only): the full-ring glow / bloom pushes pixels out of the element colour
#   range, which used to blind the reader (video 048ca5ff: a ~0.6 ring read
#   0.0 for ~3s under flashes);
# - FULL = total lit coverage >= CON_RING_COVERAGE_FULL with the largest gap
#   <= CON_FULL_MAX_GAP_SECTORS, confirmed on TWO different frames within
#   CON_FULL_CONFIRM_WINDOW whose lit-sector signatures MATCH -- a moving
#   field sweep (SK's Stellarealm) cannot hold the same bridged sectors across
#   two frames, a static full ring trivially can;
# - frames are declared UNTRUSTED (and the last trusted value held, bounded by
#   CON_HOLD_MAX_AGE) on pollution (element-coloured pixels flooding outside
#   the annulus), on a white flash covering the portrait core, or on a
#   whiteout (bright frame with the element mask starved).
# =============================================================================

# Annulus band of the concerto ring inside the con_full crop, as fractions of
# the crop height (calibrated for the (1431,1942)-(1557,2068) box at 4K; the
# crop scales with resolution so the fractions hold).
CON_RING_R_INNER = 0.35119
CON_RING_R_OUTER = 0.42261
# Angular resolution. 72 sectors = 5 degrees each; at 1080p the band holds
# ~9-12 pixels per sector, enough for a meaningful density.
CON_RING_SECTORS = 72
# A sector is LIT when at least this fraction of its band pixels are
# ring-coloured (and at least CON_SECTOR_MIN_PIXELS absolute, so a 1-2 pixel
# speckle can never light a sector at 4K where sectors are ~45 px).
CON_SECTOR_DENSITY_LIT = 0.15
CON_SECTOR_MIN_PIXELS = 2
# FULL geometry: total lit coverage AND bounded largest gap. Coverage alone
# would pass a 95% ring; the gap bound (2 sectors = 10 degrees) is what
# separates "full with an anchor notch / AA dropout" from "visibly not done".
CON_RING_COVERAGE_FULL = 0.95
CON_FULL_MAX_GAP_SECTORS = 2
# The frame is VFX-POLLUTED when more than this fraction of the ELEMENT-
# coloured pixels sit outside the annulus band; a real ring concentrates its
# pixels inside the band (measured 0.11-0.20 on genuine full reads), a screen
# flash or Iuno's Full Moon Domain arcs flood the crop. 0.5 let the domain
# arcs fake a full ring; 0.35 still clears every genuine read by ~2x.
CON_RING_POLLUTION_MAX = 0.35
# Near-white floor for the bright/glow mask (all three channels >= this).
CON_BRIGHT_RING_FLOOR = 220
# A white FLASH covers the portrait core, a ring glow does not: when more than
# this fraction of the core disc is bright, the bright mask is meaningless and
# the frame is untrusted.
CON_BRIGHT_CORE_FLASH = 0.30
# WHITEOUT / FOREIGN OVERLAY: a bright band with the element mask starved is
# the reader being blinded, not an empty ring -- the read is garbage-LOW.
# Covers both a white flash blooming the ring out of its colour range (video
# 048ca5ff, band ~200+) and the decoy star-ring overlay that hides the gauge
# (user screenshot 'its not actually full': deep out-of-range gold, band
# ~150-160). A genuinely EMPTY ring track is DARK (~50-70), well under this
# floor, so real empties still read 0 trusted.
CON_WHITEOUT_BRIGHTNESS = 120
CON_WHITEOUT_MAX_ELEMENT_LIT = 0.20
# The two full sightings must land on different frames this close together.
# Read cadence during top-offs is 0.05-0.3s and the engine's almost-full path
# re-reads after 0.05s -- but the CAPTURE cadence is what actually spaces
# distinct frames, and under load it can drop to a few fps: with the old 0.6s
# window each armed sighting expired before the next distinct frame arrived
# and a genuine full sat at 0.99 forever ('detecting a full concerto as 99').
# The signature match is the real anti-sweep gate; the window only bounds
# staleness, so it can afford low-fps tolerance.
CON_FULL_CONFIRM_WINDOW = 1.5
# The two sightings' lit-sector signatures must agree on at least this
# fraction of sectors: a sweep bridging the gap moves between frames, a full
# ring is static.
CON_FULL_SIGNATURE_MATCH = 0.95
# On untrusted frames the last trusted value is held at most this long; past
# it, fall back to the raw (capped) reading so a chronic misclassification can
# never freeze the value forever.
CON_HOLD_MAX_AGE = 2.5
# For elements with a glow band (con_glow_colors): the TRUE full ring's pale
# bloom is a brief PULSE, not a steady state -- measured on log 2d376d07 the
# genuine full sat at bloom 0.01 for seconds with peaks of only 0.21-0.74
# every few seconds, so a per-frame gate (the old 0.5) rejected genuine fulls
# for 5s+ ('still reading full concerto as 99'). A full-geometry ring is
# accepted as GENUINE when ANY of:
#   - the ring was seen at a clean PARTIAL fill earlier this visit (a genuine
#     full GROWS from partials; the decoy overlay appears fully-formed at
#     switch-in and never shows a partial),
#   - this frame's bloom reaches CON_FULL_BLOOM_MIN (a pulse peak; decoy
#     measures a constant ~0.01),
#   - a peak was seen within CON_BLOOM_MEMORY (pulse trough between peaks).
# Otherwise it is the decoy overlay: a blind frame, held.
CON_FULL_BLOOM_MIN = 0.15
CON_BLOOM_MEMORY = 8.0


def _largest_arc_run(covered):
    """Length of the longest CONTIGUOUS run of covered sectors on a circle.

    ``covered`` is a boolean sequence of angular bins. The concerto ring fills as
    one contiguous arc, so its true fill is the longest run -- scattered VFX
    speckles light isolated sectors but cannot extend the contiguous arc.
    """
    n = len(covered)
    if n == 0:
        return 0
    if all(covered):
        return n
    # rotate so index 0 is an uncovered bin -> no run wraps the boundary
    first_gap = next(i for i, c in enumerate(covered) if not c)
    rolled = [covered[(first_gap + i) % n] for i in range(n)]
    max_run = run = 0
    for c in rolled:
        run = run + 1 if c else 0
        if run > max_run:
            max_run = run
    return max_run


def _largest_gap_run(covered):
    """Longest contiguous run of UNCOVERED sectors (0 when fully covered)."""
    if not covered:
        return 0
    return _largest_arc_run([not c for c in covered])


def _close_single_gaps(lit):
    """Bridge ISOLATED one-sector holes in the lit vector (circular closing).

    Real rings drop single sectors to anti-aliasing, the fill seam, or a pixel
    of overlapping HUD -- and one such hole SPLITS the contiguous run, collapsing
    a 95% read to wherever the hole happens to sit (bootstrap sim: a ring at
    0.95 fill read 0.60 off one unlucky sector at 1080p).

    DECOY-SAFETY: this does NOT help the decoy overlay pass as full. The full
    criteria already tolerate gaps <= CON_FULL_MAX_GAP_SECTORS (2), so any
    decoy that closing could bridge (single holes) passed full geometry with
    or without it -- and the bloom gate, not geometry, is what rejects the
    decoy. Closing only stabilises PARTIAL reads. A genuine gap of >= 2
    sectors (10 degrees) survives untouched, and a sweep's isolated LIT
    sectors are the inverse case, which closing never helps.
    """
    n = len(lit)
    if n < 3:
        return list(lit)
    return [lit[i] or (lit[(i - 1) % n] and lit[(i + 1) % n]) for i in range(n)]


def _color_range_to_bgr_bounds(color_range):
    """{'r': (lo,hi), 'g': ..., 'b': ...} -> (lower, upper) BGR uint8 arrays.

    Mirrors ok.color_range_to_bound's convention (frames are BGR); kept local so
    the pure ring analysis below is testable without the game stack.
    """
    lower = np.array([color_range['b'][0], color_range['g'][0], color_range['r'][0]],
                     dtype=np.uint8)
    upper = np.array([color_range['b'][1], color_range['g'][1], color_range['r'][1]],
                     dtype=np.uint8)
    return lower, upper


_con_geometry_cache = {}


def _con_geometry(h, w):
    """Precomputed annulus/core masks and sector bins for a crop size.

    Cached per (h, w): the crop size is constant per resolution, and the
    reader runs every frame in top-off loops.
    """
    key = (h, w)
    cached = _con_geometry_cache.get(key)
    if cached is not None:
        return cached
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    dy = yy - cy
    dx = xx - cx
    dist = np.sqrt(dx * dx + dy * dy)
    in_band = (dist >= h * CON_RING_R_INNER) & (dist <= h * CON_RING_R_OUTER)
    core = dist < h * CON_RING_R_INNER * 0.8
    bins = ((np.arctan2(dy, dx) + np.pi) / (2 * np.pi) * CON_RING_SECTORS
            ).astype(np.int64) % CON_RING_SECTORS
    band_bins = bins[in_band]
    band_per_sector = np.bincount(band_bins, minlength=CON_RING_SECTORS)
    cached = (in_band, core, bins, band_bins, band_per_sector)
    _con_geometry_cache[key] = cached
    if len(_con_geometry_cache) > 8:  # defensive: never grow unbounded
        _con_geometry_cache.pop(next(iter(_con_geometry_cache)))
    return cached


def con_ring_profile(cropped, lower, upper, glow_lower=None, glow_upper=None):
    """Pure frame analysis of the concerto ring crop. THE measurement.

    Args:
        cropped: BGR crop of the con_full box.
        lower/upper: BGR bounds of the element's ring colour.
        glow_lower/glow_upper: optional BGR bounds of the element's BLOOMED
            full-ring rendering (see con_glow_colors) -- unioned into the lit
            mask exactly like the near-white bright mask.

    Returns a dict:
        lit:          per-sector booleans (element-or-glow-or-bright density lit)
        total_lit:    fraction of sectors lit
        largest_run:  longest contiguous lit arc, as a fraction (== the
                      geometric concerto fill for a partial ring)
        max_gap:      longest contiguous unlit run, in SECTORS
        element_lit_total: fraction of sectors lit by the element-or-glow masks
                      (near-white bright excluded) -- the whiteout detector's
                      input
        pollution:    fraction of element-coloured pixels OUTSIDE the band
                      (0.0 when the element mask is starved: no evidence is
                      not evidence of flooding)
        brightness:   mean grayscale of the band
        bright_core:  fraction of the core disc that is near-white (flash tell)
    """
    h, w = cropped.shape[:2]
    in_band, core, bins, band_bins, band_per_sector = _con_geometry(h, w)

    element = np.all((cropped >= lower) & (cropped <= upper), axis=2)
    if glow_lower is not None:
        element_or_glow = element | np.all((cropped >= glow_lower)
                                           & (cropped <= glow_upper), axis=2)
    else:
        element_or_glow = element
    bright = np.all(cropped >= CON_BRIGHT_RING_FLOOR, axis=2)

    element_total = int(np.count_nonzero(element))
    element_in_band = int(np.count_nonzero(element & in_band))
    pollution = (1.0 - element_in_band / element_total) if element_total >= 8 else 0.0

    core_px = int(np.count_nonzero(core))
    bright_core = (int(np.count_nonzero(bright & core)) / core_px) if core_px else 0.0

    union_band = (element_or_glow | bright) & in_band
    lit_per_sector = np.bincount(bins[union_band], minlength=CON_RING_SECTORS)
    element_per_sector = np.bincount(bins[element_or_glow & in_band], minlength=CON_RING_SECTORS)
    if glow_lower is not None:
        bloom = np.all((cropped >= glow_lower) & (cropped <= glow_upper), axis=2) | bright
    else:
        bloom = bright
    bloom_per_sector = np.bincount(bins[bloom & in_band], minlength=CON_RING_SECTORS)

    safe_band = np.maximum(band_per_sector, 1)
    density = lit_per_sector / safe_band
    lit = ((density >= CON_SECTOR_DENSITY_LIT)
           & (lit_per_sector >= CON_SECTOR_MIN_PIXELS)
           & (band_per_sector > 0))
    element_density = element_per_sector / safe_band
    element_lit = ((element_density >= CON_SECTOR_DENSITY_LIT)
                   & (element_per_sector >= CON_SECTOR_MIN_PIXELS)
                   & (band_per_sector > 0))
    bloom_density = bloom_per_sector / safe_band
    bloom_lit = ((bloom_density >= CON_SECTOR_DENSITY_LIT)
                 & (bloom_per_sector >= CON_SECTOR_MIN_PIXELS)
                 & (band_per_sector > 0))

    lit_list = _close_single_gaps(lit.tolist())
    band_gray = cropped[in_band].mean() if np.any(in_band) else 0.0
    return {
        'lit': lit_list,
        'total_lit': float(np.count_nonzero(lit)) / CON_RING_SECTORS,
        'largest_run': _largest_arc_run(lit_list) / CON_RING_SECTORS,
        'max_gap': _largest_gap_run(lit_list),
        'element_lit_total': float(np.count_nonzero(element_lit)) / CON_RING_SECTORS,
        'bloom_lit_total': float(np.count_nonzero(bloom_lit)) / CON_RING_SECTORS,
        'expect_bloom': glow_lower is not None,
        'pollution': float(pollution),
        'brightness': float(band_gray),
        'bright_core': float(bright_core),
    }


class ConReadState:
    """Per-task state for the concerto reader: frame memo, full-confirm
    sightings, the last trusted value for untrusted-frame holds, and the
    genuine-ring evidence for the bloom/decoy gate (partials seen this visit,
    last bloom-peak time)."""
    __slots__ = ('char_key', 'frame_key', 'memo', 'full_sight_t',
                 'full_sight_frame', 'full_sight_lit', 'trusted', 'trusted_t',
                 'partial_seen', 'bloom_full_t')

    def __init__(self):
        self.reset()

    def reset(self):
        self.char_key = None
        self.frame_key = None
        self.memo = None
        self.full_sight_t = 0.0
        self.full_sight_frame = None
        self.full_sight_lit = None
        self.trusted = None
        self.trusted_t = 0.0
        self.partial_seen = False
        self.bloom_full_t = 0.0


def _signature_match(a, b):
    """Fraction of sectors on which two lit signatures agree."""
    if not a or not b or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a)


def resolve_con_reading(state, profile, now, frame_key, char_key):
    """Turn one frame's ring profile into the reported concerto percent.

    Pure state machine (unit-testable): returns (percent, untrusted, reason)
    and updates ``state``. Rules:
    - a char change resets everything (sightings must never carry across a
      swap: the incoming char's ring starts empty);
    - the SAME frame returns the memoized verdict (tight loops re-read between
      next_frame calls; one frame is one sighting, never two);
    - UNTRUSTED frames (pollution / flash core / whiteout) hold the last
      trusted value for up to CON_HOLD_MAX_AGE, then fall back to the raw
      capped reading; they never stamp trust and never grant a full sighting,
      but they do NOT clear an armed sighting (a polluted frame between two
      clean fulls must not restart the confirm);
    - a clean frame meeting the FULL geometry arms a sighting; a second clean
      full on a DIFFERENT frame within CON_FULL_CONFIRM_WINDOW whose
      lit-signature matches the first confirms -> 1. Until confirmed it reads
      0.99, which the engine's almost-full path (sleep 0.05, re-read) and
      StrictRotation's confirm_con_full turn into the confirming second read
      naturally;
    - a clean NON-full frame clears the sighting and reports the contiguous
      arc directly (capped 0.99) -- no baseline, no calibration.
    """
    if state.char_key != char_key:
        state.reset()
        state.char_key = char_key
    if state.frame_key == frame_key and state.memo is not None:
        return state.memo
    state.frame_key = frame_key

    polluted = profile['pollution'] > CON_RING_POLLUTION_MAX
    flash = profile['bright_core'] > CON_BRIGHT_CORE_FLASH
    whiteout = (profile['brightness'] > CON_WHITEOUT_BRIGHTNESS
                and profile['element_lit_total'] < CON_WHITEOUT_MAX_ELEMENT_LIT
                and not profile['total_lit'] >= CON_RING_COVERAGE_FULL)
    # flash overrides the bright-union: when the core is flooded white the
    # bright mask is meaningless, so a "full" built on it cannot be trusted.
    untrusted = polluted or flash or whiteout
    if untrusted:
        reason = ('polluted' if polluted else 'flash' if flash else 'whiteout')
        if state.trusted is not None and now - state.trusted_t <= CON_HOLD_MAX_AGE:
            result = (state.trusted, True, f'{reason}: holding last trusted')
        else:
            result = (min(profile['largest_run'], 0.99), True,
                      f'{reason}: no recent trusted value, raw capped')
        state.memo = result
        return result

    full_geometry = (profile['total_lit'] >= CON_RING_COVERAGE_FULL
                     and profile['max_gap'] <= CON_FULL_MAX_GAP_SECTORS)
    if full_geometry and profile.get('expect_bloom'):
        # DECOY overlay vs GENUINE full (user screenshot read con_full_100 on
        # a visibly not-full gauge): the decoy star ring renders INSIDE the
        # element colour range, static and fully-formed AT SWITCH-IN, so
        # neither the colour masks nor the signature confirm can reject it.
        # A genuine full differs in two measurable ways (log 2d376d07):
        # it GROWS from partial fills observed earlier in the visit, and its
        # pale bloom PULSES (peaks 0.21-0.74 every few seconds vs the decoy's
        # constant ~0.01). Accept the full when either signal is present;
        # remember a peak for CON_BLOOM_MEMORY so the troughs between pulses
        # stay accepted.
        if profile.get('bloom_lit_total', 0.0) >= CON_FULL_BLOOM_MIN:
            state.bloom_full_t = now
        genuine = (state.partial_seen
                   or now - state.bloom_full_t <= CON_BLOOM_MEMORY)
        if not genuine:
            # No partial history, no bloom evidence: the decoy hiding the
            # gauge -- a blind frame, not a full and not a zero. An armed
            # sighting's timestamp is refreshed when the signature still
            # matches (a pulse trough right after a peak must not let the
            # confirm age out); it is never ARMED from here.
            if (state.full_sight_frame is not None
                    and _signature_match(state.full_sight_lit, profile['lit'])
                    >= CON_FULL_SIGNATURE_MATCH):
                state.full_sight_t = now
            reason = 'element-only full ring (decoy overlay)'
            if state.trusted is not None and now - state.trusted_t <= CON_HOLD_MAX_AGE:
                result = (state.trusted, True, f'{reason}: holding last trusted')
            else:
                result = (0.99, True, f'{reason}: no recent trusted value')
            state.memo = result
            return result
    if full_geometry:
        prior_ok = (state.full_sight_frame is not None
                    and state.full_sight_frame != frame_key
                    and now - state.full_sight_t <= CON_FULL_CONFIRM_WINDOW
                    and _signature_match(state.full_sight_lit, profile['lit'])
                    >= CON_FULL_SIGNATURE_MATCH)
        state.full_sight_t = now
        state.full_sight_frame = frame_key
        state.full_sight_lit = profile['lit']
        if prior_ok:
            percent, reason = 1, 'full confirmed (2 clean matching frames)'
        else:
            percent, reason = 0.99, 'full seen once, awaiting confirm'
    else:
        state.full_sight_frame = None
        state.full_sight_lit = None
        percent = min(profile['largest_run'], 0.99)
        reason = 'clean partial (contiguous arc)'
        if 0.05 <= percent <= 0.9:
            # a clearly-partial ring observed this visit: a later full is a
            # ring that FILLED, not the fully-formed-at-entry decoy overlay.
            state.partial_seen = True
    state.trusted = percent
    state.trusted_t = now
    result = (percent, False, reason)
    state.memo = result
    return result


class BaseCombatTask(CombatCheck):
    """基础战斗任务类，封装了游戏"鸣潮"中角色自动化操作的通用逻辑。"""
    hot_key_verified = False  # 热键是否已验证
    # NOTE: the old persisted per-element full-size baseline (con_full_size
    # Config) is GONE with the area channel: the geometric reader measures the
    # fill fraction directly and has nothing to calibrate, drift, or poison.
    freeze_durations = []  # 记录冻结/卡肉的持续时间

    def __init__(self, *args, **kwargs):
        """初始化战斗任务。

        Args:
            *args: 传递给父类的参数。
            **kwargs: 传递给父类的关键字参数。
        """
        super().__init__(*args, **kwargs)
        self.chars = [None, None, None]  # 角色列表
        self.char_texts = ['char_1_text', 'char_2_text', 'char_3_text']  # 角色文本标识符列表
        self.mouse_pos = None  # 当前鼠标位置
        self.combat_start = 0  # 战斗开始时间戳
        self.add_text_fix({'Ｅ': 'e'})
        self.use_liberation = True
        # concerto reader state (see get_current_con): the untrusted flag is
        # public API (held-value frames), the state object is internal.
        self.con_read_untrusted = False
        self._con_read_state = None

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        """添加冻结持续时间。用于精确计算技能冷却等。

        Args:
            start (float): 冻结开始时间。
            duration (float, optional): 冻结持续时间。如果为-1.0, 则根据当前时间计算。默认为 -1.0。
            freeze_time (float, optional): 认为发生冻结的最小持续时间。默认为 0.1。
        """
        if duration < 0:
            duration = time.time() - start
        if start > 0 and duration > freeze_time:
            current_time = time.time()
            self.freeze_durations = [item for item in self.freeze_durations if item[0] > current_time - 60]
            self.freeze_durations.append((start, duration, freeze_time))

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        """计算扣除冻结时间后经过的时间。

        Args:
            start (float): 开始时间戳。
            intro_motion_freeze (bool, optional): 是否考虑角色入场动画的特殊冻结。默认为 False。

        Returns:
            float: 扣除冻结后实际经过的时间 (秒)。
        """
        if start < 0:
            return 10000
        to_minus = 0
        for freeze_start, duration, freeze_time in self.freeze_durations:
            if start < freeze_start:
                if intro_motion_freeze:
                    if freeze_time == -100:
                        freeze_time = 0
                elif freeze_time == -100:
                    continue
                to_minus += duration - freeze_time
        if to_minus != 0:
            self.log_debug(f'time_elapsed_accounting_for_freeze to_minus {to_minus}')
        return time.time() - start - to_minus

    def send_key_and_wait_animation(self, key, check_function, total_wait=7, enter_animation_wait=0.6):
        """发送按键并等待动画完成。

        Args:
            key (str): 要发送的按键。
            check_function (callable): 检查动画是否结束的函数，返回 True 表示动画已结束。
            total_wait (int, optional): 总等待超时时间 (秒)。默认为 7。
            enter_animation_wait (float, optional): 进入动画的等待时间 (秒)。默认为 0.6。
        """
        start = time.time()
        animation_start = 0
        while time.time() - start < total_wait:
            if check_function():
                if animation_start > 0:
                    self._in_liberation = False
                    logger.debug(f'animation ended')
                    return
                else:
                    if time.time() - start > enter_animation_wait:
                        logger.info(f'send_key_and_wait_animation failed to enter animation')
                        return
                    logger.debug(f'animation not started send key {key}')
                    self.send_key(key, after_sleep=0.1)
            else:
                if animation_start == 0:
                    animation_start = time.time()
                    logger.debug(f'animation started: {animation_start}')
                self._in_liberation = True
            self.next_frame()
        logger.info(f'send_key_and_wait_animation timed out {key}')

    cd_cache_seconds = 1.0  # how long one OCR reading of the cd bar stays valid

    def refresh_cd(self):
        if self.scene.cd_refreshed:
            return
        index = self.get_current_char().index
        cds = self.cds.get(index)
        if cds is not None and time.time() - cds.get('time', 0) < self.cd_cache_seconds:
            # a recent reading is still valid: get_cd counts it down with
            # time_elapsed_accounting_for_freeze, and casting a skill calls
            # invalidate_cd, so re-running OCR every frame buys nothing
            self.scene.cd_refreshed = True
            return
        if cds is None:
            cds = {}
            self.cds[index] = cds
        cds['time'] = time.time()
        cds['resonance'] = 0
        cds['liberation'] = 0
        cds['echo'] = 0
        texts = self.ocr(0.81, 0.86, 0.97, 0.93, frame_processor=isolate_white_text_to_black, match=cd_regex)
        for text in texts:
            cd = convert_cd(text)
            if text.x < self.width_of_screen(0.86):
                cds['resonance'] = cd
            elif text.x > self.width_of_screen(0.91):
                cds['liberation'] = cd
            else:
                cds['echo'] = cd
        self.scene.cd_refreshed = True
        self.log_debug(f'cd refreshed: {cds} {time.time() - cds["time"]}')

    def invalidate_cd(self, char_index=None):
        """Drop the cached cd reading so the next query re-reads the UI.

        Called after sending a skill key: the cached reading predates the cast
        and would otherwise report the skill as still available for up to
        cd_cache_seconds.
        """
        if char_index is None:
            char = self.get_current_char()
            char_index = char.index if char else None
        if char_index is not None:
            self.cds.pop(char_index, None)
        self.scene.cd_refreshed = False

    def get_cd(self, box_name, char_index=None):
        self.refresh_cd()
        if char_index is None:
            char_index = self.get_current_char().index
        if cds := self.cds.get(char_index):
            time_elapsed = self.time_elapsed_accounting_for_freeze(cds['time'])
            return cds[box_name] - time_elapsed
        else:
            return 0

    def close_revive_popup(self):
        """关闭角色死亡弹窗。

        优先点击弹窗按钮 (避免 ESC 注入偶发不生效)，依次尝试:
        cancel_button → btn_dialog_close → 最后回退 ESC。
        供 BaseCombatTask 与 DomainTask 的死亡恢复共用, 保证关弹窗稳定。

        Returns:
            bool: True 表示通过点击按钮关闭, False 表示回退到了 ESC。
        """
        if self.wait_click_feature('cancel_button_hcenter_vcenter',
                                   raise_if_not_found=False,
                                   time_out=1.2,
                                   click_after_delay=0.2,
                                   threshold=0.7):
            return True
        btn_dialog_close = self.find_one('btn_dialog_close', threshold=0.8)
        if btn_dialog_close:
            self.click(btn_dialog_close, move_back=True)
            return True
        self.send_key('esc', after_sleep=2)
        self.sleep(1)
        return False

    def revive_action(self):
        """角色死亡恢复：关闭弹窗 → 传最近传送点回血。"""
        try:
            self.close_revive_popup()  # ① 关闭复活弹窗 (点按钮优先, esc 兜底)
            self.revive_at_tower_and_heal()
            logger.info(f'revive_action success')
            return True
        except Exception as e:
            logger.error(f'revive_action failed', e)
            return False

    def get_revive_search_boss_name(self):
        revive_search_names = {
            'zh_CN': '无冠者',
            'zh_TW': '無冠者',
            'en_US': 'Crownless',
        }
        return revive_search_names.get(self.game_lang, '无冠者')

    def revive_at_tower_and_heal(self):
        """搜索对应语言的无冠者/Crownless→探测打开地图→找最近传送点回血。

        不再依赖已被移除的 go_to_tower。改用 F2 图鉴搜索对应语言的目标名称后点"探测"，
        游戏会把地图定位到固定位置，从该位置寻找传送点回血，结果稳定可复现。
        只点一次"探测"，等待地图打开后再操作，防止二次点击误触地图上的传送图标。
        前提：调用前已回到大世界 (副本内死亡需先退本)。
        """
        # 退本后可能仍在加载黑屏, 给足超时等待真正回到大世界 (原 go_to_tower 用 80s)
        self.ensure_main(time_out=120)
        # ① F2 图鉴 → 全部怪物 → 搜索对应语言的无冠者
        gray_book = self.openF2Book("gray_book_all_monsters")
        self.click_box(gray_book, after_sleep=1)
        self.click(0.13, 0.14, after_sleep=0.5)        # 搜索图标
        self.input_text(self.get_revive_search_boss_name())
        self.sleep(0.3)
        self.click(0.20, 0.14, after_sleep=0.3)         # 点搜索框确保焦点
        self.send_key('enter', after_sleep=0.5)          # 回车确认搜索, 刷新结果列表
        self.click(0.13, 0.24, after_sleep=0.5)         # 选中第一条结果
        # ② 点"探测"打开地图并定位到目标 boss (只点一次, 避免地图打开后误触传送点)
        self.click(0.89, 0.92, after_sleep=1)
        # ③ 等待地图打开 (检测地图传送点), 若未打开则补点一次兜底
        if not self.wait_until(lambda: self.find_best_match_in_box(
                self.box_of_screen(0.1, 0.1, 0.9, 0.9),
                ['map_way_point', 'map_way_point_big'], 0.6) is not None,
                time_out=4, raise_if_not_found=False):
            logger.warning('revive_at_tower_and_heal: map not opened, retry探测')
            self.click(0.89, 0.92, after_sleep=1)
        # ④ 在已打开的地图上找最近传送点回血
        self._travel_to_nearest_waypoint()

    def teleport_to_heal(self):
        """按 M 开图, 就近找传送点回血 (供 FarmEchoTask 在 boss 点使用)。"""
        self.ensure_main(time_out=10)
        self.log_info('click m to open the map')
        start = time.time()
        while self.in_team_and_world() and time.time() - start < 20:
            self.send_key('m', after_sleep=2)
        self._travel_to_nearest_waypoint()

    def _travel_to_nearest_waypoint(self):
        """在已打开的地图界面上, 找最近传送点并传送, 等回到大世界。"""
        self.sleep(2)
        teleport = self.find_best_match_in_box(self.box_of_screen(0.1, 0.1, 0.9, 0.9),
                                               ['map_way_point', 'map_way_point_big'], 0.6)
        if not teleport:
            raise RuntimeError(f'Can not find a teleport to heal')
        self.click(teleport, after_sleep=1)
        travel = self.wait_feature('gray_teleport', raise_if_not_found=True, time_out=3)
        if not travel:
            pop_up = self.find_feature('map_way_point', box='map_way_point_pop_up_box')
            if pop_up:
                self.click(pop_up, after_sleep=1)
                travel = self.wait_feature('gray_teleport', raise_if_not_found=True, time_out=3)
        if not travel:
            raise RuntimeError(f'Can not find the travel button')
        self.click_box(travel, relative_x=1.5)
        self.wait_in_team_and_world(time_out=20)
        self.sleep(2)

    def raise_not_in_combat(self, message):
        """抛出未在战斗状态的异常。

        Args:
            message (str): 异常信息。
            exception_type (Exception, optional): 要抛出的异常类型。默认为 NotInCombatException。
        """
        exception_type = None
        logger.error(message)
        if self.wait_feature('revive_confirm_hcenter_vcenter', threshold=0.8, time_out=2):
            self.log_info('raise_not_in_combat char dead')
            if self.reset_to_false(reason=message):
                logger.error(f'reset to false failed: {message}')
            from src.task.AutoCombatTask import AutoCombatTask
            if not isinstance(self, AutoCombatTask) and self.revive_action():
                exception_type = CharRevivedException
                self.info_set('Revive', 'Success')
            else:
                exception_type = CharDeadException
                self.info_set('Revive', 'Failed')
        elif self.reset_to_false(reason=message):
            logger.error(f'reset to false failed: {message}')
        if exception_type is None:
            exception_type = NotInCombatException
        raise exception_type(message)

    def available(self, name, check_color=True, check_cd=True):
        """检查指定名称的技能或动作是否可用 (通过颜色百分比和冷却时间判断)。

        Args:
            name (str): 技能或动作的名称 (例如 'resonance', 'echo')。

        Returns:
            bool: 如果可用则返回 True, 否则 False。
        """
        if check_color:
            current = self.box_highlighted(name)
        else:
            current = 1
        if current > 0 and (not check_cd or not self.has_cd(name)):
            return True

    def box_highlighted(self, name):
        current = self.calculate_color_percentage(text_white_color,
                                                  self.get_box_by_name(f'box_{name}'))
        if current > 0:
            current = 1
        else:
            current = 0
        return current

    def combat_once(self, wait_combat_time=200, raise_if_not_found=True):
        """执行一次完整的战斗流程。

        Args:
            wait_combat_time (int, optional): 等待进入战斗状态的超时时间 (秒)。默认为 200。
            raise_if_not_found (bool, optional): 如果未找到战斗状态是否抛出异常。默认为 True。
        """
        if wait_combat_time > 0:
            self.wait_until(self.in_combat, time_out=wait_combat_time, raise_if_not_found=raise_if_not_found)
        self.load_chars()
        self.info['Combat Count'] = self.info.get('Combat Count', 0) + 1
        try:
            while self.in_combat():
                logger.debug(f'combat_once loop {self.chars}')
                self.get_current_char().perform()
        except CharDeadException as e:
            raise e
        except NotInCombatException as e:
            logger.info(f'combat_once out of combat break {e}')
        self.combat_end()
        self.switch_healer()
        self.wait_in_team_and_world(time_out=10, raise_if_not_found=False)

    def run_in_circle_to_find_echo(self, circle_count=3):
        """通过绕圈移动来尝试拾取声骸。

        Args:
            circle_count (int, optional): 绕圈的次数。默认为 3。

        Returns:
            bool: 如果成功拾取到声骸则返回 True, 否则 False。
        """
        directions = ['w', 'a', 's', 'd']
        step = 0.8
        duration = 0.8
        total_index = 0
        for count in range(circle_count):
            logger.debug(f'running first circle_count{circle_count} circle {total_index} duration:{duration}')
            for direction in directions:
                if total_index > 2 and (total_index + 1) % 2 == 0:
                    if not (count == circle_count - 1 and direction == directions[-1]):
                        duration += step

                if self.send_key_and_wait_f(direction, False, time_out=duration, running=True,
                                            target_text=self.absorb_echo_text()):
                    if self.pick_f():
                        return True
                total_index += 1

    def _oldest_switch_target(self, chars):
        chars = [char for char in chars if char is not None]
        if not chars:
            return None
        return min(chars, key=lambda char: (char.last_switch_in_time, char.index))

    def _switch_rule_3_target(self, candidates, allow_healer=True):
        healers_without_buff = [
            char for char in candidates
            if allow_healer and char.is_healer and char.buff_time > 0 and not char.has_buff()
        ]
        if healers_without_buff:
            return self._oldest_switch_target(healers_without_buff)

        sub_dps_without_buff = [
            char for char in candidates
            if char.is_sub_dps and char.buff_time > 0 and not char.has_buff()
        ]
        if sub_dps_without_buff:
            return self._oldest_switch_target(sub_dps_without_buff)

        main_dps = [char for char in candidates if char.is_main_dps]
        if main_dps:
            return self._oldest_switch_target(main_dps)

        return self._oldest_switch_target(candidates)

    def _target_has_switch_cd(self, char):
        return char.time_elapsed_accounting_for_freeze(char.last_switch_time) <= 1

    def _buff_remaining(self, char):
        if char.buff_time <= 0 or not char.has_buff():
            return 0
        return max(0, char.buff_time - char.time_elapsed_accounting_for_freeze(char.last_buff_time))

    def _lowest_buff_remaining_target(self, candidates):
        buffers = [char for char in candidates if not char.is_main_dps and char.buff_time > 0]
        if not buffers:
            return None
        return min(buffers, key=lambda char: (self._buff_remaining(char), char.last_switch_in_time, char.index))

    def _unbuffed_non_main_target(self, current_char, candidates):
        if current_char.is_main_dps or current_char.buff_time <= 0:
            return None
        unbuffed_non_main = [
            char for char in candidates
            if not char.is_main_dps and char.buff_time > 0
               and not char.has_buff()
        ]
        return self._oldest_switch_target(unbuffed_non_main)

    def _choose_intro_switch_target(self, must_targets, normal_targets):
        if must_targets:
            return self._oldest_switch_target(must_targets)
        for char_type in ('is_main_dps', 'is_sub_dps', 'is_healer'):
            target = self._oldest_switch_target([char for char in normal_targets if getattr(char, char_type)])
            if target:
                return target
        return None

    def _choose_switch_target_by_buff_time(self, current_char, candidates):
        if not candidates:
            return current_char

        if current_char.is_main_dps:
            lowest_buff_remaining = self._lowest_buff_remaining_target(candidates)
            if lowest_buff_remaining:
                return lowest_buff_remaining

        unbuffed_non_main = self._unbuffed_non_main_target(current_char, candidates)
        if unbuffed_non_main:
            return unbuffed_non_main

        if current_char.is_sub_dps or current_char.is_healer:
            main_dps = [char for char in candidates if char.is_main_dps]
            if main_dps:
                return self._oldest_switch_target(main_dps)

        return self._switch_rule_3_target(candidates)

    def _choose_switch_target(self, current_char, has_intro, target_low_con=False):
        candidates = [
            char for char in self.chars
            if char is not None and char != current_char
        ]
        if not candidates:
            return current_char

        must_targets = []
        normal_targets = []
        no_targets = []
        for char in candidates:
            switch_priority = char.get_switch_priority(current_char=current_char, has_intro=has_intro,
                                                       target_low_con=target_low_con)
            logger.debug(f'switch_next_char hook: {char} priority {switch_priority}')
            if switch_priority == SwitchPriority.MUST:
                must_targets.append(char)
            elif switch_priority == SwitchPriority.NO:
                no_targets.append(char)
            else:
                normal_targets.append(char)

        if has_intro:
            return self._choose_intro_switch_target(must_targets, normal_targets) or current_char

        if must_targets:
            candidates = must_targets
        else:
            candidates = normal_targets
            if not candidates:
                return current_char

        candidates_without_switch_cd = [char for char in candidates if not self._target_has_switch_cd(char)]
        if candidates_without_switch_cd:
            candidates = candidates_without_switch_cd

        return self._choose_switch_target_by_buff_time(current_char, candidates)

    def _apply_intro_flags(self, current_char, switch_to, has_intro):
        switch_to.has_intro = has_intro
        switch_to.has_sub_dps_intro = has_intro and current_char.is_sub_dps

    def switch_next_char(self, current_char, post_action=None, free_intro=False, target_low_con=False):
        """切换到下一个最优角色。

        Args:
            current_char (BaseChar): 当前角色对象。
            post_action (callable, optional): 切换后执行的动作 (回调函数)。默认为 None。
            free_intro (bool, optional): 是否强制认为拥有入场技 (通常在协奏值满时)。默认为 False。
            target_low_con (bool, optional): 是否优先切换到协奏值较低的角色。默认为 False。
        """
        has_intro = free_intro
        current_con = 0
        self.update_lib_portrait_icon()
        # buff stamps land right before swaps (outro amps, SK's lib field):
        # force a fresh overlay so the countdown reflects this swap immediately
        self._last_buff_overlay = 0
        self.update_buff_overlay()
        if not has_intro:
            current_con = current_char.get_current_con()
            if current_con > 0.8 and current_con != 1:
                logger.info(f'switch_next_char current_con {current_con:.2f} almost full, sleep and check again')
                self.sleep(0.05)
                self.next_frame()
                current_con = current_char.get_current_con()
            if current_con == 1:
                has_intro = True

        switch_to = self._choose_switch_target(current_char, has_intro, target_low_con=target_low_con)
        if not switch_to or switch_to == current_char:
            logger.warning(f"{current_char} can't find next char to switch to, performing too fast add a normal attack")
            current_char.continues_normal_attack(0.2)
            return
        self._apply_intro_flags(current_char, switch_to, has_intro)
        logger.info(
            f'switch_next_char {current_char}({current_char.char_type}) -> {switch_to}({switch_to.char_type}) '
            f'has_intro {switch_to.has_intro} has_sub_dps_intro {switch_to.has_sub_dps_intro} '
            f'current_con {current_con}')
        # if self.debug:
        #     self.screenshot(f'switch_next_char_{current_con}')
        from src.char.ShoreKeeper import ShoreKeeper
        last_click = 0
        start = time.time()
        while True:
            if not (isinstance(switch_to, ShoreKeeper) and has_intro):
                self.check_combat()
            now = time.time()
            in_team, current_index, _ = self.in_team()
            if not in_team:
                # the team UI can drop out for a few frames (liberation flash,
                # intro VFX); keep waiting instead of aborting combat on the
                # first missed frame, and don't click into unknown screens
                if now - start > self.switch_char_time_out:
                    logger.info(f'not in team while switching chars_{current_char}_to_{switch_to} {now - start}')
                    self.raise_not_in_combat(f'not in_team while switching')
                self.next_frame()
                continue
            if current_index == current_char.index:
                self.update_lib_portrait_icon()
                refreshed_has_intro = has_intro or current_char.is_con_full()
                if refreshed_has_intro != has_intro:
                    has_intro = refreshed_has_intro
                    switch_to = self._choose_switch_target(current_char, has_intro,
                                                           target_low_con=target_low_con)
                    if not switch_to or switch_to == current_char:
                        logger.warning(
                            f"{current_char} can't find next char to switch to after intro refresh, "
                            f"performing too fast add a normal attack")
                        current_char.continues_normal_attack(0.2)
                        return
                    logger.info(f'switch_next_char refreshed target after intro became available: {switch_to}')
                self._apply_intro_flags(current_char, switch_to, has_intro)
                if has_intro:
                    current_char.f_break(check_f_on_switch=True)

            if now - last_click > 0.1:
                self.send_key(switch_to.index + 1)
                self.sleep(0.001)
                last_click = now
                self.log_debug('switch not detected, send click')
                self.click()
                self.sleep(0.001)
                # re-check right after the key so a successful switch is
                # detected in the same iteration without waiting a frame
                in_team, new_index, _ = self.in_team()
                if in_team:
                    current_index = new_index
            if current_index != switch_to.index:
                if now - start > 10:
                    if self.debug:
                        self.screenshot(f'switch_not_detected_{current_char}_to_{switch_to}')
                    self.raise_not_in_combat('failed switch chars')
            else:
                self.in_liberation = False
                if not has_intro:
                    current_char.f_break(check_f_on_switch=True)
                current_char.switch_out(con_full=has_intro)
                switch_to.is_current_char = True
                switch_to.last_switch_in_time = time.time()
                if has_intro:
                    current_time = time.time()
                    self.add_freeze_duration(current_time, switch_to.intro_motion_freeze_duration, -100)
                    current_char.last_outro_time = current_time
                break
            self.next_frame()

        if post_action:
            logger.debug(f'post_action {post_action}')
            post_action(switch_to, has_intro)
        logger.info(f'switch_next_char end {(current_char.last_switch_time - start):.3f}s')

    def find_mouse_forte(self):
        return self.find_one('mouse_forte', horizontal_variance=0.025, vertical_variance=0.015, threshold=0.6,
                             frame_processor=binarize_for_matching)

    def find_e_forte(self):
        box = self.find_one('e_forte', horizontal_variance=0.025, threshold=0.6,
                            frame_processor=binarize_for_matching)
        if not box:
            return None
        # The utility-wheel prompt renders an E keycap in the same screen slot,
        # flanked by a slash and a mouse icon whose middle button is highlighted
        # yellow; the forte prompt is a bare E. Reject the wheel context via the
        # yellow neighbor (measured: wheel fixtures >=0.07, bare forte E 0.000).
        neighbor = Box(box.x + int(box.width * 1.8), box.y - int(box.height * 0.3),
                       int(box.width * 1.6), int(box.height * 1.6), name='e_forte_neighbor')
        yellow = self.calculate_color_percentage(wheel_mouse_yellow, neighbor)
        if yellow > 0.04:
            self.log_debug(f'find_e_forte rejected wheel prompt, yellow={yellow:.3f}')
            return None
        return box

    def get_liberation_key(self):
        """获取共鸣解放技能的按键。

        Returns:
            str: 共鸣解放技能的按键字符串。
        """
        return self.key_config['Liberation Key']

    def get_echo_key(self):
        """获取声骸技能的按键。

        Returns:
            str: 声骸技能的按键字符串。
        """
        return self.key_config['Echo Key']

    def get_resonance_key(self):
        """获取共鸣技能的按键。

        Returns:
            str: 共鸣技能的按键字符串。
        """
        return self.key_config['Resonance Key']

    def has_resonance_cd(self):
        """检查共鸣技能是否在冷却中。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.has_cd('resonance')

    def has_cd(self, box_name, char_index=None):
        """检查指定UI区域是否处于冷却状态 (通过检测特定颜色的点和数字)。

        Args:
            box_name (str): UI区域的名称 (例如 'resonance', 'echo', 'liberation')。

        Returns:
            bool: 如果在冷却中则返回 True, 否则 False。
        """
        return self.get_cd(box_name, char_index) > 0.2

    def get_current_char(self, raise_exception=False) -> BaseChar:
        """获取当前操作的角色对象。

        Args:
            raise_exception (bool, optional): 如果找不到当前角色是否抛出异常。默认为 True。

        Returns:
            BaseChar: 当前角色对象 (`BaseChar`) 或 None。
        """
        for char in self.chars:
            if char and char.is_current_char:
                return char
        if raise_exception and not self.in_team()[0]:
            self.raise_not_in_combat('can find current char!!')
        # self.load_chars()
        return None

    def combat_end(self):
        """战斗结束时调用的清理方法。"""
        self.clear_buff_overlay()
        current_char = self.get_current_char(raise_exception=False)
        if current_char:
            self.get_current_char().on_combat_end(self.chars)

    def switch_healer(self):
        if self.config.get('Switch to Healer after Combat'):
            current_char = self.get_current_char()
            if current_char and not current_char.is_healer:
                current_char.switch_other_char()

    def sleep_check(self):
        """休眠指定时间, 并在休眠前后检查战斗状态。

        Args:
            timeout (float): 休眠的秒数。
            check_combat (bool, optional): 是否在休眠前检查战斗状态。默认为 True。
        """
        if self.skip_combat_check:
            return
        # self.log_debug(f'sleep_check {self._in_combat}')
        if self._in_combat:
            self.update_buff_overlay()
            self.next_frame()
            if not self.in_combat():
                self.raise_not_in_combat('sleep check not in combat')

    # Refresh cadence of the on-screen buff countdown overlay. sleep_check runs
    # on every action sleep while in combat, so the overlay ticks roughly twice
    # a second without adding a hot-loop cost.
    BUFF_OVERLAY_INTERVAL = 0.5

    def update_buff_overlay(self):
        """Render the live team-buff countdowns as a PANEL at the bottom right.

        Drawn as one opaque dark image patch via the overlay's blur-patch
        channel (draw_boxes can only paint 2px outlines in 3 colours -- the
        first draw_boxes version rendered as an unreadable white block). Each
        buff keeps a fixed row: label, a bar that empties with the remaining
        fraction of its duration, and the seconds left. Bar colour: green =
        comfortably live, amber = under the burst-gate margin (4s), dark =
        expired/never applied. Throttled by BUFF_OVERLAY_INTERVAL; the panel
        is cleared at combat end.
        """
        now = time.time()
        if now - getattr(self, '_last_buff_overlay', 0) < self.BUFF_OVERLAY_INTERVAL:
            return
        self._last_buff_overlay = now
        try:
            import cv2
            from ok.gui.Communicate import communicate
        except Exception:  # pragma: no cover - headless/test environment
            return
        from src.combat.BuffTracker import (get_buff_tracker, DURATIONS,
                                            SK_LIBERATION, SK_OUTRO, IUNO_OUTRO,
                                            IUNO_DOMAIN, AUGUSTA_OUTRO)
        rows = (('SK Lib', SK_LIBERATION), ('SK Outro', SK_OUTRO),
                ('Iuno Outro', IUNO_OUTRO), ('Moon Dom', IUNO_DOMAIN),
                ('Aug Outro', AUGUSTA_OUTRO))
        tracker = get_buff_tracker(self)
        w = max(240, int(self.width_of_screen(0.11)))
        row_h = max(20, int(w * 0.11))
        pad = row_h // 2
        h = pad * 2 + row_h * len(rows)
        panel = np.full((h, w, 3), 24, dtype=np.uint8)          # dark BGR bg
        bar_x = int(w * 0.36)
        bar_w = w - bar_x - int(w * 0.17)
        font = cv2.FONT_HERSHEY_SIMPLEX
        fs = row_h / 42.0
        for i, (label, name) in enumerate(rows):
            y = pad + i * row_h
            remaining = tracker.remaining(name)
            duration = DURATIONS.get(name) or 30.0
            frac = max(0.0, min(1.0, remaining / duration))
            if remaining <= 0:
                color = (70, 70, 70)                              # expired: dark
            elif remaining <= 4.0:
                color = (60, 170, 245)                            # amber: about to drop
            else:
                color = (110, 220, 120)                           # green: live
            cv2.putText(panel, label, (pad, y + int(row_h * 0.72)), font, fs,
                        (235, 235, 235), 1, cv2.LINE_AA)
            cv2.rectangle(panel, (bar_x, y + int(row_h * 0.22)),
                          (bar_x + bar_w, y + int(row_h * 0.80)), (58, 58, 58), -1)
            if frac > 0:
                cv2.rectangle(panel, (bar_x, y + int(row_h * 0.22)),
                              (bar_x + int(bar_w * frac), y + int(row_h * 0.80)),
                              color, -1)
            cv2.putText(panel, f'{remaining:2.0f}s',
                        (bar_x + bar_w + 4, y + int(row_h * 0.72)), font, fs,
                        (235, 235, 235), 1, cv2.LINE_AA)
        x0 = int(self.width_of_screen(0.99)) - w
        y0 = int(self.height_of_screen(0.86)) - h
        communicate.blur_overlay.emit([(x0, y0, w, h, panel)])

    def clear_buff_overlay(self):
        """Remove the buff panel from the overlay (combat over)."""
        try:
            from ok.gui.Communicate import communicate
            communicate.clear_blur_overlay.emit()
        except Exception:  # pragma: no cover - headless/test environment
            pass

    def check_combat(self):
        """检查当前是否处于战斗状态, 如果不是则抛出异常。"""
        if self.skip_combat_check:
            return
        if self._in_combat and not self.in_combat():
            # if self.debug:
            #     self.screenshot('not_in_combat_calling_check_combat')
            self.raise_not_in_combat('combat check not in combat')

    def set_key(self, key, box):
        best = self.find_best_match_in_box(box, ['t', 'e', 'r', 'q'], threshold=0.7)
        logger.debug(f'set_key best match {key}: {best}')
        if best and best.name != self.key_config[key]:
            self.key_config[key] = best.name
            self.log_info(f'set_key {key} to {best.name}')

    def has_short_action(self):
        """是否有短动作条"""
        return self.find_one(self.get_target_names()[0], box='target_box_short', threshold=0.6)

    def load_hotkey(self, force=False):
        """加载或自动设置游戏内技能热键。

        Args:
            force (bool, optional): 是否强制重新加载热键。默认为 False。
        """
        if not self.hot_key_verified or force:
            self.hot_key_verified = True
            scale = 1.2
            if not self.has_short_action():
                # self.set_key('Resonance Key', self.get_box_by_name('e').scale(scale))
                self.set_key('Echo Key', self.get_box_by_name('r').scale(scale))
                self.set_key('Liberation Key', self.get_box_by_name('q').scale(scale))
                # self.set_key('Tool Key', self.get_box_by_name('t').scale(scale))

            self.info_set('Liberation Key', self.get_liberation_key())
            # self.info_set('Resonance Key', self.get_resonance_key())
            self.info_set('Echo Key', self.get_echo_key())
            # self.info_set('Tool Key', self.key_config['Tool Key'])
        return self.key_config

    def has_char(self, char_cls):
        for char in self.chars:
            if isinstance(char, char_cls):
                return char

    def load_chars(self):
        """加载队伍中的角色信息。"""
        self.load_hotkey()
        in_team, current_index, count = self.in_team()
        if not in_team:
            return
        previous_char_identity = self._char_identity(self.chars)
        # self.log_info('load chars')
        self.chars[0] = get_char_by_pos(self, self.get_box_by_name('box_char_1'), 0, safe_get(self.chars, 0))
        self.chars[1] = get_char_by_pos(self, self.get_box_by_name('box_char_2'), 1, safe_get(self.chars, 1))

        if count == 3:
            new_char = get_char_by_pos(self, self.get_box_by_name('box_char_3'), 2, safe_get(self.chars, 2))
            if len(self.chars) == 2:
                self.chars.append(new_char)
            else:
                self.chars[2] = new_char
        else:
            if len(self.chars) == 3:
                self.chars = self.chars[:2]
                logger.info(f'team size changed to 2')

        for char in self.chars:
            if char is not None:
                char.reset_state()
                if char.index == current_index:
                    char.is_current_char = True
                else:
                    char.is_current_char = False
        self.combat_start = time.time()
        if len(self.chars) >= 2:
            if self._char_identity(self.chars) != previous_char_identity:
                translated_names = []
                for c in self.chars:
                    if c is not None:
                        class_name = c.name
                        official_name = mismatched_names.get(class_name, class_name)
                        # 单元测试时 self._app 为 None，此时不进行翻译，直接回传原名
                        translated_name = self.tr(official_name) if self._app is not None else official_name
                        translated_names.append(translated_name)
                self.info_set('Chars', ', '.join(translated_names))
                for c in self.chars:
                    self.log_info(f'loaded chars success {c} {c.confidence}')
            return True

    @staticmethod
    def _char_identity(chars):
        return tuple((char.char_name, char.name) if char is not None else None for char in chars)

    @staticmethod
    def should_update(the_char, old_char):
        """判断是否应该更新角色对象 (例如, 识别到新角色或角色类型变化)。

        Args:
            the_char (BaseChar): 新的角色对象。
            old_char (BaseChar): 旧的角色对象。

        Returns:
            bool: 如果需要更新则返回 True, 否则 False。
        """
        return (type(the_char) is BaseChar and old_char is None) or (
                type(the_char) is not BaseChar and old_char != the_char)

    def box_resonance(self):
        """获取共鸣技能冷却UI区域的盒子对象。

        Returns:
            Box: 盒子对象。
        """
        return self.get_box_by_name('box_resonance_cd')

    def get_resonance_cd_percentage(self):
        """获取共鸣技能冷却UI区域白色像素百分比。

        Returns:
            float: 白色像素百分比。
        """
        return self.calculate_color_percentage(white_color, self.get_box_by_name('box_resonance_cd'))

    def get_resonance_percentage(self):
        """获取共鸣技能UI区域可用状态的白色像素百分比。

        Returns:
            float: 白色像素百分比。
        """
        return self.calculate_color_percentage(white_color, self.get_box_by_name('box_resonance'))

    def is_con_full(self):
        """检查当前角色的协奏值是否已满。

        Ring geometry only, two-frame confirmed (see resolve_con_reading). A
        portrait-marker template channel (con_full_* in the char's con_mark
        box) was tried as a false-negative rescue and REMOVED: log 835f001c
        showed it matching the element flourish in the char's slot ~75ms after
        a swap-away (con actually 0.0) and NOT matching during a genuine
        on-field full -- wrong in both directions.

        Returns:
            bool: 如果协奏值已满则返回 True, 否则 False。
        """
        return self.get_current_con() == 1

    def _con_state(self):
        state = getattr(self, '_con_read_state', None)
        if state is None:
            state = ConReadState()
            self._con_read_state = state
        return state

    def _ensure_ring_index(self, cropped=None):
        """确保当前角色协奏值环的颜色索引已识别。

        Reworked: identification counts pixels INSIDE THE ANNULUS BAND only
        (the old whole-box percentage let Iuno's ring-coloured domain arcs and
        element VFX outside the band pick the wrong colour), requires a
        minimum of evidence before CACHING (a starved frame -- swap blur,
        flash -- returns -1 for this read instead of latching a garbage index
        forever), and HEALS a wrong cache: if the cached colour is completely
        absent from the band while another colour has strong evidence, the
        index is re-stamped. ring_index doubles as the ELEMENT id for the
        lib_ready/portrait templates, so a wrong latch hurts far beyond the
        con read.

        Returns:
            int: 协奏值环的颜色索引 (-1 when it cannot be identified yet).
        """
        char = self.get_current_char(raise_exception=False)
        if char is None:
            return -1
        box = self.get_con_box()
        if cropped is None:
            cropped = box.crop_frame(self.frame)
        h, w = cropped.shape[:2]
        in_band = _con_geometry(h, w)[0]
        # evidence floor: ~40 band pixels at the 4K crop, scaled by crop area
        min_evidence = max(8, int(40 * (h * w) / (126 * 126)))
        counts = []
        for color_range in con_colors:
            lower, upper = _color_range_to_bgr_bounds(color_range)
            element = np.all((cropped >= lower) & (cropped <= upper), axis=2)
            counts.append(int(np.count_nonzero(element & in_band)))
        best_index = int(np.argmax(counts))
        cached = char.ring_index
        if cached >= 0:
            if counts[cached] == 0 and counts[best_index] >= min_evidence:
                self.logger.warning(
                    f'_ensure_ring_index healing {char}: cached '
                    f'{con_templates[cached]} absent from the band, '
                    f'{con_templates[best_index]} has {counts[best_index]} px')
                char.ring_index = best_index
            return char.ring_index
        if counts[best_index] >= min_evidence:
            char.ring_index = best_index
            self.log_debug(
                f'_ensure_ring_index {char} to {char.ring_index} '
                f'{con_templates[best_index]} ({counts[best_index]} px)')
        return char.ring_index

    def get_con_box(self):
        """获取协奏值能量环的UI区域盒子对象。

        Returns:
            Box: 盒子对象。
        """
        return self.box_of_screen_scaled(3840, 2160, 1431, 1942, 1557, 2068, name='con_full',
                                         hcenter=True)

    def get_current_con(self):
        """获取当前角色的协奏值百分比。

        Reworked (see the module header above the constants): ONE geometric
        measurement (per-sector band density -> contiguous arc) resolved
        through a small state machine (untrusted-frame holds, two-frame
        signature-matched full confirm). No area channel, no persisted
        baseline, no rescue patches.

        Returns:
            float: 协奏值百分比 (0.0 到 1.0); exactly 1 only on a confirmed full.
        """
        box = self.get_con_box()
        box.confidence = 0
        cropped = box.crop_frame(self.frame)
        target_index = self._ensure_ring_index(cropped)

        if 0 <= target_index < len(con_colors):
            candidate_indexes = [target_index]
        else:
            # identity unknown this frame (starved/blurred): read all colours
            # and take the strongest profile; the index caches on the next
            # clean frame.
            candidate_indexes = range(len(con_colors))
        profile = None
        for i in candidate_indexes:
            lower, upper = _color_range_to_bgr_bounds(con_colors[i])
            glow = con_glow_colors[i]
            glow_lower = glow_upper = None
            if glow is not None:
                glow_lower, glow_upper = _color_range_to_bgr_bounds(glow)
            p = con_ring_profile(cropped, lower, upper, glow_lower, glow_upper)
            if profile is None or (p['total_lit'], -p['pollution']) > \
                    (profile['total_lit'], -profile['pollution']):
                profile = p

        char = self.get_current_char(raise_exception=False)
        # Frame identity for the memo / two-sighting confirm. id(self.frame)
        # alone is NOT enough: capture backends can reuse the same buffer for
        # every frame (constant id -> the second sighting is rejected as "the
        # same frame" and the memo freezes -- a genuine full then reads 0.99
        # forever). Pair the id with a cheap content checksum of the crop so a
        # reused buffer with NEW content is a new frame; a genuinely duplicated
        # frame still memoizes as one sighting.
        frame_key = (id(self.frame), int(cropped[::5, ::5].sum()))
        percent, untrusted, reason = resolve_con_reading(
            self._con_state(), profile, time.time(), frame_key,
            id(char) if char is not None else None)
        self.con_read_untrusted = untrusted
        self.log_debug(
            f'get_current_con {percent:.2f} [{reason}] lit={profile["total_lit"]:.2f} '
            f'run={profile["largest_run"]:.2f} gap={profile["max_gap"]} '
            f'bloom={profile["bloom_lit_total"]:.2f} '
            f'pol={profile["pollution"]:.2f} br={profile["brightness"]:.0f} '
            f'core={profile["bright_core"]:.2f}')

        box.confidence = percent
        self.draw_boxes(f'is_con_full_{self}', box)
        return percent

    def con_ring_metrics(self, cropped, color_range, sectors=72):
        """Compat wrapper over con_ring_profile: (contiguous arc fraction,
        pollution fraction). The ``sectors`` argument is kept for signature
        compatibility; the profile uses CON_RING_SECTORS."""
        lower, upper = _color_range_to_bgr_bounds(color_range)
        profile = con_ring_profile(cropped, lower, upper)
        self.log_debug(f'con_ring_metrics arc={profile["largest_run"]:.2f} '
                       f'outside={profile["pollution"]:.2f}')
        return profile['largest_run'], profile['pollution']

    def con_ring_angularly_full(self, cropped, color_range, sectors=72,
                                coverage=CON_RING_COVERAGE_FULL):
        """Whether the ring is angularly complete AND the frame is clean enough
        to trust (compat wrapper; full geometry = coverage + bounded gap)."""
        lower, upper = _color_range_to_bgr_bounds(color_range)
        profile = con_ring_profile(cropped, lower, upper)
        return (profile['total_lit'] >= coverage
                and profile['max_gap'] <= CON_FULL_MAX_GAP_SECTORS
                and profile['pollution'] <= CON_RING_POLLUTION_MAX
                and profile['bright_core'] <= CON_BRIGHT_CORE_FLASH)

    def update_lib_portrait_icon(self):
        # self.ensure_con_lib_boxes()
        for i, char in enumerate(self.chars):
            char_index = i + 1
            if char is None:
                continue
            if not char.is_current_char and char.ring_index >= 0 and not char._liberation_available:
                box = self.get_box_by_name("lib_mark_char_{}".format(char_index))
                match = self.find_one(lib_ready_templates[char.ring_index], box=box, threshold=0.8)
                if match:
                    char._liberation_available = True
                    self.log_debug('checking liberation_available by template {} {}'.format(char, match))
                    # self.screenshot('liberation_available_{}_{}_{}'.format(char, match.name, match.confidence))


white_color = {  # 用于检测UI元素可用状态的白色颜色范围。
    'r': (253, 255),  # Red range
    'g': (253, 255),  # Green range
    'b': (253, 255)  # Blue range
}

wheel_mouse_yellow = {  # 工具轮盘提示中鼠标中键图标的黄色高亮。
    'r': (200, 255),
    'g': (150, 230),
    'b': (0, 110)
}

con_colors = [  # 不同角色属性的协奏值能量环的颜色范围列表。
    {
        'r': (205, 235),
        'g': (190, 222),  # for yellow spectro
        'b': (90, 130)
    },
    {
        'r': (150, 190),  # Red range
        'g': (95, 140),  # Green range for purple electric
        'b': (210, 249)  # Blue range
    },
    {
        'r': (200, 230),  # Red range
        'g': (100, 130),  # Green range    for red fire
        'b': (75, 105)  # Blue range
    },
    {
        'r': (60, 95),  # Red range
        'g': (150, 180),  # Green range    for blue ice
        'b': (210, 245)  # Blue range
    },
    {
        'r': (70, 110),  # Red range
        'g': (215, 250),  # Green range    for green wind
        'b': (155, 190)  # Blue range
    },
    {
        'r': (190, 220),  # Red range
        'g': (65, 105),  # Green range    for havoc
        'b': (145, 175)  # Blue range
    }
]

# BLOOMED full-ring renderings, parallel to `con_colors` (None = element mask
# suffices). SK's TRUE full ring (user screenshot: 'sk 100 concerto') renders
# PALE bloomed gold -- blue channel ~130-200, far above the element range's
# 90-130 cap yet below the near-white bright floor (220), so it matched
# NEITHER mask and a genuine full read ~0. The blue FLOOR of this band (110)
# simultaneously excludes the decoy star-ring state, whose deep saturated
# gold sits at blue <~80 (user screenshot: 'its not actually full').
con_glow_colors = [
    {
        'r': (190, 255),
        'g': (180, 250),  # pale bloomed gold for spectro full ring
        'b': (135, 210)   # blue FLOOR above the element range's 130 cap, so the
                          # glow mask can never light on element-coloured pixels
                          # (the decoy ring renders in-range -- see below)
    },
    None,  # electric
    None,  # fire
    None,  # ice
    None,  # wind
    None,  # havoc
]

con_templates = [  # 协奏值能量环的模板名称列表 (对应 `con_colors`)。
    'con_spectro',
    'con_electric',
    'con_fire',
    'con_ice',
    'con_wind',
    'con_havoc',
]

lib_ready_templates = [  # 头像右边大招可用对号
    'lib_ready_spectro',  # 3
    'lib_ready_electric',  # 3
    'lib_ready_fire',  # 2
    'lib_ready_ice',  # 2
    'lib_ready_wind',  # 1
    'lib_ready_havoc',  # 3
]

con_full_templates = [  # 头像右边表示当前角色 协奏满
    'con_full_spectro',  # 3
    'con_full_electric',  # 3
    'con_full_fire',  # 2
    'con_full_ice',  # 2
    'con_full_wind',  # 1
    'con_full_havoc',  # 3
]


def convert_cd(text):
    """
    Strips a string to only keep the first part that matches the regex pattern.
    Args:
      text: The input string.
      pattern: The regex pattern to match.
    Returns:
      The first matching substring, or None if no match is found.
    """
    try:
        return float(text.name)
    except ValueError:
        match = re.search(cd_regex, text.name)
        if match:
            return float(match.group(0))
        else:
            return 1
