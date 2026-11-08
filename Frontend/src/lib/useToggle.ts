import { useState } from "react";

// Open/closed for a popover. Its own hook so the console's tests can show the popover open.
export function useToggle(initial = false): [boolean, (open: boolean) => void] {
  return useState(initial);
}
