import { useState } from "react";

const PREFIX = "homefront:draft:";

interface Stored<T> {
  values: T;
  step: number;
  savedAt: string;
}

function read<T>(key: string): Stored<T> | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as Stored<T>) : null;
  } catch {
    return null; // storage unavailable or corrupt: behave as if nothing was saved
  }
}

type Updater<V> = V | ((prev: V) => V);

/**
 * Holds a multi-step form's answers and current step in React state.
 *
 * - `save()` snapshots answers + step to sessionStorage (this tab only, gone when it closes),
 *   so progress survives navigating away and back.
 * - `clear()` wipes both the live state and the snapshot.
 * - `forget()` drops only the snapshot (used after a successful submit).
 *
 * `initial` must be a stable reference (declare it at module level).
 */
export function useFormDraft<T extends object>(key: string, initial: T) {
  const [snapshot] = useState(() => read<T>(key));
  const [values, setValues] = useState<T>(() => ({ ...initial, ...snapshot?.values }));
  const [step, setStep] = useState(snapshot?.step ?? 1);
  const [savedAt, setSavedAt] = useState<string | null>(snapshot?.savedAt ?? null);

  const setField = <K extends keyof T>(field: K, next: Updater<T[K]>) =>
    setValues((cur) => ({
      ...cur,
      [field]: typeof next === "function" ? (next as (p: T[K]) => T[K])(cur[field]) : next,
    }));

  const save = () => {
    const stamp = new Date().toISOString();
    try {
      sessionStorage.setItem(PREFIX + key, JSON.stringify({ values, step, savedAt: stamp }));
    } catch {
      return false;
    }
    setSavedAt(stamp);
    return true;
  };

  const forget = () => {
    try {
      sessionStorage.removeItem(PREFIX + key);
    } catch {
      /* ignore */
    }
    setSavedAt(null);
  };

  const clear = () => {
    forget();
    setValues(initial);
    setStep(1);
  };

  return { values, setField, step, setStep, savedAt, save, clear, forget };
}
