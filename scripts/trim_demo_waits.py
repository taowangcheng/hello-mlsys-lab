#!/usr/bin/env python3
"""Trim only recorded model waits, retaining original footage and an edit list."""
import argparse
import json
from pathlib import Path
import subprocess


def duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True))


def edit(folder):
    evidence = json.loads((folder / "evidence.json").read_text())
    if evidence["outcome"] != "complete":
        raise ValueError("Only completed recordings can be published")
    source = folder / "walkthrough.webm"
    total = duration(source)
    cuts = []
    for wait in sorted(evidence["waits"], key=lambda item: item["start"]):
        start, end = max(0, wait["start"]), min(total, wait["end"])
        if end - start > 35:
            cut = {"start": start + 7, "end": end - 7, "reason": wait["kind"]}
            if cuts and cut["start"] < cuts[-1]["end"]:
                raise ValueError("Wait intervals must not overlap")
            cuts.append(cut)
    keep, cursor = [], 0
    for cut in cuts:
        keep.append((cursor, cut["start"]))
        cursor = cut["end"]
    keep.append((cursor, total))
    filters = []
    count = len(keep)
    if count > 1:
        filters.append(f"[0:v]split={count}" + "".join(f"[s{i}]" for i in range(count)))
    for i, (start, end) in enumerate(keep):
        stream = f"s{i}" if count > 1 else "0:v"
        filters.append(f"[{stream}]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]")
    filters.append("".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[out]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", "-i", str(source),
        "-filter_complex", ";".join(filters), "-map", "[out]", "-an", "-c:v", "libx264", "-threads", "4",
        "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(folder / "walkthrough.mp4")], check=True)
    edited = duration(folder / "walkthrough.mp4")
    expected = sum(end - start for start, end in keep)
    if abs(edited - expected) > .3:
        raise ValueError(f"Unexpected speed or duration: {edited}, expected {expected}")

    def translate(seconds):
        return seconds - sum(max(0, min(seconds, cut["end"]) - cut["start"]) for cut in cuts)

    raw_chapters = evidence["chapters"]
    chapters = [{**chapter, "seconds": translate(chapter["seconds"])} for chapter in raw_chapters]
    for name, data in (("chapters.raw.json", raw_chapters), ("chapters.json", chapters), ("edit-list.json", {
            "source": source.name, "output": "walkthrough.mp4", "playback_speed": 1,
            "source_duration_s": total, "output_duration_s": edited, "removed": cuts,
            "kept": [{"start": start, "end": end} for start, end in keep],
            "note": "Only the middle of recorded model waits was removed. UI actions, replies and confirmations retain their original speed."})):
        (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"{folder.name}: {total:.1f}s -> {edited:.1f}s, {len(cuts)} wait cuts, 1x", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", type=Path, nargs="+")
    args = parser.parse_args()
    for folder in args.folders:
        edit(folder)
