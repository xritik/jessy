import { useCallback, useEffect, useRef, useState } from "react";

function getRecognitionCtor() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

/**
 * Wraps the browser's native SpeechRecognition API. Not supported in
 * all browsers (Firefox lacks it) — always check `isSupported` before
 * showing mic controls.
 */
export function useSpeechToText({ lang = "en-US", onFinalResult } = {}) {
  const RecognitionCtor = getRecognitionCtor();
  const isSupported = Boolean(RecognitionCtor);

  const [isListening, setIsListening] = useState(false);
  const [interimText, setInterimText] = useState("");
  const [error, setError] = useState(null);

  const recognitionRef = useRef(null);
  // Keep the latest callback in a ref so the recognition instance
  // (created once) always calls the freshest handler without us
  // having to tear it down and rebuild it every render.
  const onFinalResultRef = useRef(onFinalResult);
  useEffect(() => {
    onFinalResultRef.current = onFinalResult;
  }, [onFinalResult]);

  useEffect(() => {
    if (!isSupported) return;

    const recognition = new RecognitionCtor();
    recognition.lang = lang;
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onstart = () => {
      setIsListening(true);
      setError(null);
      setInterimText("");
    };

    recognition.onresult = (event) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += transcript;
        } else {
          interim += transcript;
        }
      }
      if (interim) setInterimText(interim);
      if (final) {
        setInterimText("");
        onFinalResultRef.current?.(final.trim());
      }
    };

    recognition.onerror = (event) => {
      setError(event.error || "speech-recognition-error");
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
      setInterimText("");
    };

    recognitionRef.current = recognition;

    return () => {
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.abort();
      recognitionRef.current = null;
    };
  }, [lang, isSupported]);

  const start = useCallback(() => {
    if (!recognitionRef.current || isListening) return;
    try {
      recognitionRef.current.start();
    } catch {
      // start() throws if called while already active — safe to ignore.
    }
  }, [isListening]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  const toggle = useCallback(() => {
    if (isListening) stop();
    else start();
  }, [isListening, start, stop]);

  return { isSupported, isListening, interimText, error, start, stop, toggle };
}
