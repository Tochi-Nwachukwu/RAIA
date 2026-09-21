"use client";

/**
 * The dial: a ring with the station's sound drawn inside it.
 *
 * The audio elements are routed through one AnalyserNode (created on the first Tune in - a browser
 * will not start an AudioContext without a gesture) and the canvas draws the waveform every frame.
 * Reading samples from another origin needs CORS, which is why the elements are crossOrigin.
 */

import { type RefObject, useEffect, useRef } from "react";

interface Props {
  sources: RefObject<HTMLAudioElement | null>[];
  playing: boolean;
  size?: number;
}

export function Visualizer({ sources, playing, size = 300 }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const wired = useRef<WeakSet<HTMLAudioElement>>(new WeakSet());

  // Wire the elements into the analyser the first time the listener tunes in.
  useEffect(() => {
    if (!playing) return;
    type WithWebkit = typeof window & { webkitAudioContext?: typeof AudioContext };
    const Ctor = window.AudioContext || (window as WithWebkit).webkitAudioContext;
    if (!Ctor) return;
    if (!audioContext.current) {
      audioContext.current = new Ctor();
      analyser.current = audioContext.current.createAnalyser();
      analyser.current.fftSize = 2048;
      analyser.current.smoothingTimeConstant = 0.75;
      analyser.current.connect(audioContext.current.destination);
    }
    void audioContext.current.resume();
    for (const ref of sources) {
      const element = ref.current;
      if (!element || wired.current.has(element)) continue;
      try {
        audioContext.current.createMediaElementSource(element).connect(analyser.current!);
        wired.current.add(element);
      } catch {
        /* already routed through the graph */
      }
    }
  }, [playing, sources]);

  // A hidden tab can suspend the graph; bring it back when the listener returns.
  useEffect(() => {
    const wake = () => {
      if (document.visibilityState === "visible") void audioContext.current?.resume();
    };
    document.addEventListener("visibilitychange", wake);
    return () => document.removeEventListener("visibilitychange", wake);
  }, []);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const context = element.getContext("2d");
    if (!context) return;

    const ratio = window.devicePixelRatio || 1;
    element.width = size * ratio;
    element.height = size * ratio;
    context.scale(ratio, ratio);

    const middle = size / 2;
    const radius = size / 2 - 10;
    let frame = 0;
    let idle = 0;

    const render = () => {
      frame = requestAnimationFrame(render);
      const node = analyser.current;
      const samples = new Uint8Array(node ? node.fftSize : 512);
      let level = 0;
      if (node) {
        node.getByteTimeDomainData(samples);
        for (const value of samples) level += Math.abs(value - 128) / 128;
        level /= samples.length;
      } else {
        idle += 0.02;
        samples.fill(128);
      }

      context.clearRect(0, 0, size, size);

      // The ring, breathing with the sound.
      const glow = Math.min(1, level * 6);
      context.beginPath();
      context.arc(middle, middle, radius, 0, Math.PI * 2);
      context.strokeStyle = `rgba(63, 191, 143, ${0.25 + glow * 0.5})`;
      context.lineWidth = 1.5 + glow * 2.5;
      context.shadowBlur = 18 * glow;
      context.shadowColor = "rgba(63, 191, 143, 0.6)";
      context.stroke();
      context.shadowBlur = 0;

      // The waveform across the dial, clipped to the ring.
      context.save();
      context.beginPath();
      context.arc(middle, middle, radius - 2, 0, Math.PI * 2);
      context.clip();
      context.beginPath();
      const span = radius * 2 - 8;
      const points = 160; // a readable line rather than a wall of samples
      const stride = Math.max(1, Math.floor(samples.length / points));
      for (let i = 0, p = 0; i < samples.length; i += stride, p++) {
        const amplitude = node ? (samples[i] - 128) / 128 : Math.sin(p / 9 + idle) * 0.06;
        // Taper towards the edges so the line sits inside the circle.
        const along = i / samples.length;
        const taper = Math.sin(along * Math.PI);
        const x = middle - span / 2 + along * span;
        const y = middle - amplitude * radius * 0.8 * taper;
        if (p === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      }
      context.strokeStyle = `rgba(233, 249, 243, ${0.55 + glow * 0.45})`;
      context.lineWidth = 2;
      context.lineJoin = "round";
      context.shadowBlur = 12 * glow;
      context.shadowColor = "rgba(63, 191, 143, 0.7)";
      context.stroke();
      context.restore();
      context.shadowBlur = 0;
    };

    render();
    return () => cancelAnimationFrame(frame);
  }, [size]);

  return <canvas ref={canvas} style={{ width: size, height: size }} aria-hidden="true" />;
}
