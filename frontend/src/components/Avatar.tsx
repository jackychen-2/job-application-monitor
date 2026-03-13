interface AvatarProps {
  avatarUrl?: string | null;
  displayName?: string | null;
  email?: string | null;
  sizeClassName?: string;
  textClassName?: string;
}

function getInitials(displayName?: string | null, email?: string | null): string {
  const source = (displayName || email || "").trim();
  if (!source) {
    return "?";
  }

  const parts = source
    .split(/[\s@._-]+/)
    .map((part) => part.trim())
    .filter(Boolean);

  if (parts.length === 0) {
    return source.slice(0, 1).toUpperCase();
  }

  if (parts.length === 1) {
    return parts[0].slice(0, 1).toUpperCase();
  }

  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

export default function Avatar({
  avatarUrl,
  displayName,
  email,
  sizeClassName = "h-9 w-9",
  textClassName = "text-sm",
}: AvatarProps) {
  const initials = getInitials(displayName, email);

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={displayName || email || "Profile avatar"}
        className={`${sizeClassName} rounded-full object-cover`}
        referrerPolicy="no-referrer"
      />
    );
  }

  return (
    <div
      aria-hidden="true"
      className={`${sizeClassName} inline-flex items-center justify-center rounded-full bg-emerald-100 font-semibold text-emerald-700 ${textClassName}`}
    >
      {initials}
    </div>
  );
}
