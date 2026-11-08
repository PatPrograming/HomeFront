import "./Stepper.css";

// Presentational progress indicator. `current` is the 1-based active step.
interface StepperProps {
  steps: string[];
  current: number;
}

export default function Stepper({ steps, current }: StepperProps) {
  return (
    <ol className="stepper" aria-label="Progress">
      {steps.map((label, i) => {
        const n = i + 1;
        const state = n < current ? "done" : n === current ? "current" : "todo";
        return (
          <li
            key={label}
            className={`step step-${state}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className="step-dot">
              {state === "done" ? (
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                  <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                n
              )}
            </span>
            <span className="step-label">{label}</span>
          </li>
        );
      })}
    </ol>
  );
}
