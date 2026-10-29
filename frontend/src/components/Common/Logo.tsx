import { Link } from "@tanstack/react-router"
import { BookOpenText } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const icon = (
    <span
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground",
        className,
      )}
    >
      <BookOpenText className="size-4" />
    </span>
  )

  const content = (
    <span className="flex items-center gap-2.5">
      {icon}
      {variant !== "icon" && (
        <span
          className={cn(
            "text-sm font-semibold tracking-normal",
            variant === "responsive" && "group-data-[collapsible=icon]:hidden",
          )}
        >
          Grounded Research
        </span>
      )}
    </span>
  )

  return asLink ? <Link to="/">{content}</Link> : content
}
