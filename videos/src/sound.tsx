import { Audio } from "@remotion/media";
import { interpolate, Sequence, staticFile, useVideoConfig } from "remotion";

// Every file here is synthesised by scripts/make-audio.py.
export type SfxName =
  | "pop" // a sticker, pill or badge popping in
  | "click" // the cursor clicking a control
  | "key" // one keystroke while text types in
  | "whoosh" // a scene or panel sliding in
  | "ding" // the mascot, title and closing cards
  | "success" // a green check, "response recorded", verified
  | "error" // blocked, refused, a mismatch
  | "notify"; // a Slack DM or bot reply arriving

const LEVEL: Record<SfxName, number> = {
  pop: 0.35,
  click: 0.5,
  key: 0.18,
  whoosh: 0.28,
  ding: 0.4,
  success: 0.45,
  error: 0.35,
  notify: 0.4,
};

const LENGTH: Record<SfxName, number> = {
  pop: 0.2,
  click: 0.1,
  key: 0.08,
  whoosh: 0.5,
  ding: 1.4,
  success: 0.9,
  error: 0.45,
  notify: 0.6,
};

/** One sound effect, starting `at` frames into the enclosing Sequence. */
export const Sfx: React.FC<{ name: SfxName; at?: number; volume?: number }> = ({
  name,
  at = 0,
  volume = 1,
}) => {
  const { fps } = useVideoConfig();
  return (
    <Sequence from={at} durationInFrames={Math.ceil(LENGTH[name] * fps) + 2} layout="none">
      <Audio src={staticFile(`audio/${name}.wav`)} volume={LEVEL[name] * volume} />
    </Sequence>
  );
};

/** Typing sounds: one key every `every` frames from `from` for `count` keys. */
export const Typing: React.FC<{ from: number; count: number; every?: number }> = ({
  from,
  count,
  every = 2,
}) => (
  <>
    {Array.from({ length: Math.min(count, 60) }, (_, i) => (
      <Sfx key={i} name="key" at={from + i * every} volume={0.8 + ((i * 37) % 5) * 0.08} />
    ))}
  </>
);

/** The background loop, under everything, fading in and out at the ends. */
export const Music: React.FC<{ volume?: number }> = ({ volume = 0.16 }) => {
  const { durationInFrames, fps } = useVideoConfig();
  return (
    <Audio
      src={staticFile("audio/music.wav")}
      loop
      volume={(f) =>
        volume *
        interpolate(f, [0, fps, durationInFrames - 2 * fps, durationInFrames], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      }
    />
  );
};
