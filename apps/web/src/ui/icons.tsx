import type { SVGProps } from 'react';

/**
 * Icons not provided by lucide-react (which dropped brand marks). Same 24px stroke grid as lucide,
 * paths copied from the prototype's icon set.
 */
interface IconProps extends SVGProps<SVGSVGElement> {
  size?: number;
}

export function YoutubeIcon({ size = 16, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      <rect x="2" y="5" width="20" height="14" rx="4" />
      <path d="m10 9 5 3-5 3z" fill="currentColor" />
    </svg>
  );
}
