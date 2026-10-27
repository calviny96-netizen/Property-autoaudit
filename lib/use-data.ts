'use client';

import { useCallback, useEffect, useEffectEvent, useState } from 'react';
import { errorMessage, isAbort } from '@/lib/api';

/**
 * Load something when the inputs change, cancel the previous request, and keep
 * the last good data on screen while the next request runs.
 */
export function useData<T>(loader: (signal: AbortSignal) => Promise<T>, deps: readonly unknown[], enabled = true) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState('');
  // The latest loader is always used, without making the effect depend on it.
  const run = useEffectEvent((signal: AbortSignal) => loader(signal));
  const key = `${JSON.stringify(deps)}#${version}`;
  // Loading simply means: the request for the current inputs has not finished yet.
  const loading = enabled && settled !== key;

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    run(controller.signal)
      .then((value) => { if (!controller.signal.aborted) { setData(value); setError(''); } })
      .catch((reason: unknown) => { if (!isAbort(reason) && !controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setSettled(key); });
    return () => controller.abort();
  }, [key, enabled]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, setData, error, loading, reload };
}

export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
