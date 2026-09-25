import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";
import { useAppear } from "./ui";

const SLACK_FONT = "Lato, 'Helvetica Neue', Arial, sans-serif";

/** Render Slack mrkdwn: *bold*, _italic_, `code`, <url|label>. */
const inline = (line: string, keyBase: string): React.ReactNode[] => {
  const parts: React.ReactNode[] = [];
  const re = /<([^|>]+)\|([^>]+)>|`([^`]+)`|\*([^*]+)\*|(?<![A-Za-z0-9])_([^_]+)_(?![A-Za-z0-9])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(line))) {
    if (m.index > last) parts.push(line.slice(last, m.index));
    const key = `${keyBase}-${k++}`;
    if (m[2]) parts.push(<span key={key} style={{ color: "#1264A3" }}>{m[2]}</span>);
    else if (m[3])
      parts.push(
        <code key={key} style={{ color: "#E01E5A", background: "#F4F4F4", border: "1px solid #DDD", borderRadius: 4, padding: "0 4px", fontSize: "0.88em" }}>
          {m[3]}
        </code>,
      );
    else if (m[4]) parts.push(<b key={key}>{inline(m[4], key)}</b>);
    else if (m[5]) parts.push(<i key={key}>{inline(m[5], key)}</i>);
    last = m.index + m[0].length;
  }
  if (last < line.length) parts.push(line.slice(last));
  return parts;
};

/** Render Slack mrkdwn: *bold*, _italic_, `code`, <url|label>. */
export const Mrkdwn: React.FC<{ text: string }> = ({ text }) => (
  <>
    {text.split("\n").map((line, li) => (
      <div key={li} style={{ minHeight: "0.6em" }}>
        {inline(line, String(li))}
      </div>
    ))}
  </>
);

export const SlackWindow: React.FC<{
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  width?: number;
  style?: React.CSSProperties;
}> = ({ title, subtitle, children, width = 1060, style }) => (
  <div
    style={{
      width,
      borderRadius: 18,
      overflow: "hidden",
      backgroundColor: "#fff",
      color: "#1D1C1D",
      fontFamily: SLACK_FONT,
      boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
      ...style,
    }}
  >
    <div style={{ backgroundColor: "#3F0E40", color: "#fff", padding: "14px 24px", display: "flex", alignItems: "baseline", gap: 16 }}>
      <span style={{ fontSize: 28, fontWeight: 800 }}>{title}</span>
      {subtitle ? <span style={{ fontSize: 20, opacity: 0.75 }}>{subtitle}</span> : null}
    </div>
    <div style={{ padding: "8px 0 16px" }}>{children}</div>
  </div>
);

const AVATAR: Record<string, string> = { bot: "#2BAC76" };
const colorFor = (name: string) =>
  AVATAR[name] ?? ["#E8912D", "#1264A3", "#CD2553", "#4A154B", "#007A5A"][name.length % 5];

export const Msg: React.FC<{
  at: number;
  who: string;
  bot?: boolean;
  time?: string;
  text: string;
  ephemeral?: boolean;
  button?: { label: string; pressAt?: number };
  buttons?: { label: string; pressAt?: number[] }[];
  reaction?: { emoji: string; at: number };
  indent?: boolean;
  size?: number;
}> = ({ at, who, bot, time = "", text, ephemeral, button, buttons, reaction, indent, size = 24 }) => {
  const frame = useCurrentFrame();
  const p = useAppear(at);
  if (frame < at - 2) return null;
  const btns = buttons ?? (button ? [{ label: button.label, pressAt: button.pressAt ? [button.pressAt] : [] }] : []);
  return (
    <div
      style={{
        display: "flex",
        gap: 14,
        padding: "10px 24px",
        marginLeft: indent ? 56 : 0,
        borderLeft: indent ? "3px solid #DDD" : undefined,
        backgroundColor: ephemeral ? "#F8F8F8" : undefined,
        opacity: p,
        translate: `0px ${(1 - p) * 20}px`,
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 8,
          flexShrink: 0,
          backgroundColor: bot ? AVATAR.bot : colorFor(who),
          color: "#fff",
          fontWeight: 900,
          fontSize: 24,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {bot ? "CU" : who[0]}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {ephemeral ? <div style={{ fontSize: 17, color: "#616061", marginBottom: 2 }}>Only visible to you</div> : null}
        <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
          <span style={{ fontWeight: 900, fontSize: size }}>{who}</span>
          {bot ? (
            <span style={{ fontSize: 14, backgroundColor: "#E8E8E8", color: "#616061", borderRadius: 3, padding: "1px 5px", fontWeight: 700 }}>APP</span>
          ) : null}
          <span style={{ fontSize: 17, color: "#616061" }}>{time}</span>
        </div>
        <div style={{ fontSize: size, lineHeight: 1.4 }}>
          <Mrkdwn text={text} />
        </div>
        {btns.length ? (
          <div style={{ display: "flex", gap: 10, marginTop: 10 }}>
            {btns.map((b) => {
              const pressed = (b.pressAt ?? []).some((f) => frame >= f && frame < f + 10);
              const flash = (b.pressAt ?? []).reduce((acc, f) => Math.max(acc, interpolate(frame, [f, f + 4, f + 24], [0, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })), 0);
              return (
                <div
                  key={b.label}
                  style={{
                    border: `2px solid ${button ? "#007A5A" : "#BBB"}`,
                    backgroundColor: button ? "#007A5A" : "#fff",
                    color: button ? "#fff" : "#1D1C1D",
                    fontWeight: 800,
                    fontSize: 21,
                    padding: "7px 18px",
                    borderRadius: 6,
                    scale: pressed ? "0.93" : "1",
                    boxShadow: `0 0 0 ${6 * flash}px rgba(242,182,50,${0.8 * flash})`,
                  }}
                >
                  {b.label}
                </div>
              );
            })}
          </div>
        ) : null}
        {reaction && frame >= reaction.at ? (
          <div
            style={{
              display: "inline-flex",
              gap: 6,
              marginTop: 8,
              border: "1.5px solid #1264A3",
              backgroundColor: "#E8F5FA",
              borderRadius: 14,
              padding: "2px 10px",
              fontSize: 20,
              scale: `${useAppearSafe(frame, reaction.at)}`,
            }}
          >
            {reaction.emoji} <span style={{ color: "#1264A3", fontWeight: 700 }}>1</span>
          </div>
        ) : null}
      </div>
    </div>
  );
};

const useAppearSafe = (frame: number, at: number) =>
  interpolate(frame, [at, at + 8], [0.4, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

/** The line a fellow types before a slash-command reply. */
export const Typed: React.FC<{ at: number; who: string; cmd: string }> = ({ at, who, cmd }) => {
  const frame = useCurrentFrame();
  if (frame < at) return null;
  const n = Math.min(cmd.length, Math.floor((frame - at) / 2));
  return (
    <div style={{ margin: "8px 24px", border: "2px solid #BBB", borderRadius: 10, padding: "10px 16px", fontSize: 24, fontFamily: "monospace", color: "#1D1C1D", backgroundColor: "#fff" }}>
      <span style={{ color: "#616061", fontFamily: SLACK_FONT, fontSize: 18, marginRight: 12 }}>{who}:</span>
      {cmd.slice(0, n)}
      <span style={{ opacity: Math.floor(frame / 12) % 2 ? 0 : 1, color: theme.ink }}>|</span>
    </div>
  );
};
