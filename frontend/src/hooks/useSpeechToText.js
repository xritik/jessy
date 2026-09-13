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
export function useSpeechToText({ lang = "en-US", onFinalResult, wakeWord, wakeWordEnabled = false, onWakeWord, } = {}) {
  const RecognitionCtor = getRecognitionCtor();
  const isSupported = Boolean(RecognitionCtor);

  const [isListening, setIsListening] = useState(false);
  const [interimText, setInterimText] = useState("");
  const [error, setError] = useState(null);

  const recognitionRef = useRef(null);
  const wakeRecognitionRef = useRef(null);
  // Keep the latest callback in a ref so the recognition instance
  // (created once) always calls the freshest handler without us
  // having to tear it down and rebuild it every render.
  const onFinalResultRef = useRef(onFinalResult);
  const pendingStartRef = useRef(false);
useEffect(() => {
  onFinalResultRef.current = onFinalResult;
}, [onFinalResult]);

const onWakeWordRef = useRef(onWakeWord);
useEffect(() => {
  onWakeWordRef.current = onWakeWord;
}, [onWakeWord]);

useEffect(() => {
  if (!isSupported || !wakeWordEnabled || !wakeWord || isListening) return;

  const wakeRecognition = new RecognitionCtor();
  wakeRecognition.lang = lang;
  wakeRecognition.continuous = true;
  wakeRecognition.interimResults = true;

  const normalizedWakeWord = wakeWord.trim().toLowerCase();

  wakeRecognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const transcript = event.results[i][0].transcript.trim().toLowerCase();
      if (transcript.includes(normalizedWakeWord)) {
        wakeRecognition.stop();
        onWakeWordRef.current?.();
        return;
      }
    }
  };

  wakeRecognition.onerror = () => {};
  wakeRecognition.onend = () => {
    if (pendingStartRef.current) {
      pendingStartRef.current = false;
      try {
        recognitionRef.current?.start();
      } catch {
        // start() throws if called while already active — safe to ignore.
      }
      return;
    }
    if (!wakeWordEnabled || isListening) return;
    try { wakeRecognition.start(); } catch {}
  };

  wakeRecognitionRef.current = wakeRecognition;
  try { wakeRecognition.start(); } catch {}

  return () => {
    wakeRecognitionRef.current = null;
    wakeRecognition.onresult = null;
    wakeRecognition.onerror = null;
    wakeRecognition.onend = null;
    try { wakeRecognition.abort(); } catch {}
  };
}, [lang, isSupported, wakeWord, wakeWordEnabled, RecognitionCtor, isListening]);

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

    if (wakeRecognitionRef.current) {
      // Wait for the wake recognizer's onend to confirm the mic is
      // actually released before claiming it, or Chrome fires a
      // spurious "aborted" error on the new recognizer.
      pendingStartRef.current = true;
      try {
        wakeRecognitionRef.current.abort();
      } catch {
        // Already stopped — start immediately since no onend will fire.
        pendingStartRef.current = false;
        try {
          recognitionRef.current.start();
        } catch {
          // start() throws if called while already active — safe to ignore.
        }
      }
      return;
    }

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
