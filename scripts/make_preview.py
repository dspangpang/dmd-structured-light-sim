"""Package four rendered example frames as a phase-step GIF, without rescaling intensities."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from render_manifest import completed_render_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Completed four-step example output")
    parser.add_argument("--output", type=Path, required=True, help="New GIF path")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new file")
    receipt = json.loads((args.run / "run.json").read_text())
    manifest_path = args.run / receipt["render_manifest"]
    complete, reason = completed_render_manifest(manifest_path.parent)
    if receipt["status"] != "COMPLETE" or not complete:
        parser.error(f"Completed render required: {reason}")
    config = json.loads((args.run / "inputs/config.json").read_text())
    if config["patterns"].get("kind") != "sinusoidal" or config["patterns"].get("phase_steps") != 4:
        parser.error("Preview requires a generated four-step sinusoidal sequence")
    manifest = json.loads(manifest_path.read_text())
    records = manifest["artifacts"]["camera_files"]
    if len(records) != 4:
        parser.error("Expected four camera frames")
    font = ImageFont.load_default(size=20)
    frames, sources = [], []
    for i, record in enumerate(records):
        source = manifest_path.parent / record["path"]
        with Image.open(source) as image:
            image = image.convert("L")
            frame = Image.new("L", (image.width, image.height + 48), 0)
            frame.paste(image, (0, 48))
        draw = ImageDraw.Draw(frame)
        label = f"Phase {i * 90} deg   |   {i + 1} / 4"
        draw.text((frame.width / 2, 24), label, fill=220, anchor="mm", font=font)
        # One shared grayscale palette preserves every rendered intensity exactly.
        indexed = Image.new("P", frame.size)
        indexed.putpalette([value for gray in range(256) for value in (gray, gray, gray)])
        indexed.frombytes(frame.tobytes())
        frames.append(indexed)
        sources.append({"phase_deg": i * 90, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(args.output, save_all=True, append_images=frames[1:], duration=700,
                   loop=0, disposal=2, optimize=False)
    print(json.dumps({"gif": str(args.output), "projector_type": config["projector"]["type"],
                      "period_px": config["patterns"]["period_px"], "frames": sources}, indent=2))


if __name__ == "__main__":
    main()
