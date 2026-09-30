"""Align two versions of the same subtitle (AI translation vs human-final)
by their time spans.

Pure functions. The human edit may shift cue times slightly and merge/split
cues, so index-based matching is not enough: we walk both lists and group
cues into blocks whose accumulated time spans match, then label each block
``same`` / ``time_changed`` / ``split`` (1 AI cue -> N final) / ``merge``.
"""

from __future__ import annotations

from dataclasses import dataclass

from creator_agent.models.transcript import TranscriptSegment


@dataclass
class Alignment:
    ai: list[int]  # indices into the AI cue list
    final: list[int]  # indices into the human-final cue list
    kind: str  # same | time_changed | split | merge
    time_changed: bool = False  # start/end moved beyond tolerance


def align_cues(ai: list[TranscriptSegment], final: list[TranscriptSegment], tol: float = 0.05) -> list[Alignment]:
    """Group AI and final cues into time-matched blocks, in order."""
    out: list[Alignment] = []
    i = j = 0
    while i < len(ai) or j < len(final):
        # Tail on one side only: group whatever remains.
        if i >= len(ai) or j >= len(final):
            ga = list(range(i, len(ai)))
            gf = list(range(j, len(final)))
            out.append(_block(ai, ga, final, gf, tol))
            break
        ga, gf = [i], [j]
        a_end, f_end = ai[i].end, final[j].end
        # Expand the side whose span ends earlier until both spans match.
        while abs(a_end - f_end) > tol:
            if a_end < f_end:
                i += 1
                if i >= len(ai):
                    break
                ga.append(i)
                a_end = max(a_end, ai[i].end)
            else:
                j += 1
                if j >= len(final):
                    break
                gf.append(j)
                f_end = max(f_end, final[j].end)
        out.append(_block(ai, ga, final, gf, tol))
        i, j = ga[-1] + 1, gf[-1] + 1
    return out


def _block(
    ai: list[TranscriptSegment], ga: list[int], final: list[TranscriptSegment], gf: list[int], tol: float
) -> Alignment:
    time_changed = (
        bool(ga)
        and bool(gf)
        and (abs(ai[ga[0]].start - final[gf[0]].start) > tol or abs(ai[ga[-1]].end - final[gf[-1]].end) > tol)
    )
    if len(ga) == 1 and len(gf) == 1:
        kind = "time_changed" if time_changed else "same"
    elif len(ga) < len(gf):
        kind = "split"
    else:
        kind = "merge"
    return Alignment(ai=ga, final=gf, kind=kind, time_changed=time_changed)


def join_text(segments: list[TranscriptSegment], idxs: list[int]) -> str:
    return " ".join(" ".join(segments[k].text.split()) for k in idxs)
