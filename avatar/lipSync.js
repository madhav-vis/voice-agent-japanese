// Mouth level from an audio track. The analyser only listens: it is not connected to the speakers,
// so whatever already plays the track (LiveKit's <audio> element) is left alone.

const FLOOR = 0.012;  // RMS below this is silence / codec noise
const FULL = 0.2;     // RMS at which the mouth is fully open (AivisSpeech peaks around 0.2-0.25)

/** Returns { level(), close() }: level() is the current mouth opening, 0..1, read once per frame. */
export function listenToTrack(mediaStreamTrack) {
  const ctx = new AudioContext();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;  // ~21 ms at 48 kHz
  const source = ctx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
  source.connect(analyser);
  ctx.resume().catch(() => {});  // created after a click, but a context can still start suspended
  const samples = new Float32Array(analyser.fftSize);

  return {
    level() {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const s of samples) sum += s * s;
      const rms = Math.sqrt(sum / samples.length);
      return Math.min(1, Math.max(0, (rms - FLOOR) / (FULL - FLOOR)));
    },
    close() {
      source.disconnect();
      ctx.close().catch(() => {});
    },
  };
}
