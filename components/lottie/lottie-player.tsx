'use client';

import { useEffect, useRef, useSyncExternalStore } from 'react';
import type { AnimationItem } from 'lottie-web';
import { cn } from '@/lib/utils';

/**
 * Lottie animations are loaded on demand: the player and each JSON are separate
 * chunks, so a page that never shows one pays nothing. Nothing touches
 * `document` while the page is rendered on the server.
 */
const animations = {
  searching: () => import('@/lib/lottie/searching.json'),
  'empty-box': () => import('@/lib/lottie/empty-box.json'),
  success: () => import('@/lib/lottie/success.json'),
  'match-found': () => import('@/lib/lottie/match-found.json'),
  upload: () => import('@/lib/lottie/upload.json'),
  loading: () => import('@/lib/lottie/loading.json'),
  padlock: () => import('@/lib/lottie/padlock.json'),
} as const;

export type LottieName = keyof typeof animations;

// The frame shown when the person asked their device for less motion.
const restFrame: Record<LottieName, number> = {
  searching: 24, 'empty-box': 0, success: 60, 'match-found': 70, upload: 30, loading: 10, padlock: 0,
};

const reducedQuery = '(prefers-reduced-motion: reduce)';
function subscribeReduced(listener: () => void) {
  const query = window.matchMedia(reducedQuery);
  query.addEventListener('change', listener);
  return () => query.removeEventListener('change', listener);
}
export function usePrefersReducedMotion() {
  return useSyncExternalStore(subscribeReduced, () => window.matchMedia(reducedQuery).matches, () => false);
}

export function LottiePlayer({ name, className, loop = true, label, onComplete }: {
  name: LottieName;
  className?: string;
  loop?: boolean;
  /** Describe the animation for screen readers; omit when it is purely decorative. */
  label?: string;
  onComplete?: () => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  const complete = useRef(onComplete);
  useEffect(() => { complete.current = onComplete; });

  useEffect(() => {
    let animation: AnimationItem | undefined;
    let cancelled = false;
    void Promise.all([import('lottie-web/build/player/lottie_light'), animations[name]()]).then(([player, json]) => {
      if (cancelled || !container.current) return;
      animation = player.default.loadAnimation({
        container: container.current,
        renderer: 'svg',
        loop: loop && !reduced,
        autoplay: !reduced,
        animationData: structuredClone((json as { default?: object }).default ?? json),
        rendererSettings: { preserveAspectRatio: 'xMidYMid meet', progressiveLoad: true },
      });
      if (reduced) animation.goToAndStop(restFrame[name], true);
      animation.addEventListener('complete', () => complete.current?.());
    }).catch(() => { /* decoration only: the page works without it */ });
    return () => { cancelled = true; animation?.destroy(); };
  }, [name, loop, reduced]);

  return <div ref={container} className={cn('aspect-square', className)} {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })} />;
}
