import Image from "next/image";

export function BrandMark({
  className = "",
  priority = false,
}: {
  className?: string;
  priority?: boolean;
}) {
  return (
    <Image
      className={`brand-logo-image ${className}`.trim()}
      src="/brand/cluecdc-logo.png"
      alt=""
      width={1100}
      height={1100}
      sizes="48px"
      priority={priority}
      aria-hidden="true"
    />
  );
}
