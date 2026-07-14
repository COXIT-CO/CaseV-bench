import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn's class-name helper: merges conditional classes and de-dupes Tailwind ones. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
