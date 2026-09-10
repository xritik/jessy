import { useCallback, useEffect, useRef, useState } from "react";

function getSynth() {
  if (typeof window === "undefined") return null;
  return window.speechSynthesis || null;
}

/**
 * Wraps the browser's native SpeechSynthesis API. Widely supported
 * (Chrome, Edge, Safari, Firefox) — but always check `isSupported`
 * before assuming voices are available.
 */
export function useTextToSpeech() {
  const synth = getSynth();
  const isSupported = Boolean(synth);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const utteranceRef = useRef(null);

  const stop = useCallback(() => {
    if (!isSupported) return;
    synth.cancel();
    setIsSpeaking(false);
  }, [isSupported, synth]);

  const speak = useCallback(
    (text, { onEnd, rate = 1, pitch = 1 } = {}) => {
      if (!isSupported || !text) {
        onEnd?.();
        return;
      }
      synth.cancel(); // clear any queued/current speech before starting new
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = rate;
      utterance.pitch = pitch;
      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => {
        setIsSpeaking(false);
        onEnd?.();
      };
      utterance.onerror = () => {
        setIsSpeaking(false);
        onEnd?.();
      };
      utteranceRef.current = utterance;
      synth.speak(utterance);
    },
    [isSupported, synth]
  );

  // Stop any in-flight speech when the component using this hook unmounts.
  useEffect(() => {
    return () => {
      if (isSupported) synth.cancel();
    };
  }, [isSupported, synth]);

  return { isSupported, isSpeaking, speak, stop };
}
