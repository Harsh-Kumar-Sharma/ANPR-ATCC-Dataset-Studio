interface Props {
  /** 0-1, as jobs report it. Converted to a percentage here so no caller
   *  has to remember which of the two units it is holding. */
  fraction: number;
  label: string;
  className: string;
}

function ProgressBar({ fraction, label, className }: Props) {
  const percent = Math.round(fraction * 100);
  return (
    <div
      className={className}
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className={`${className}-fill`} style={{ width: `${percent}%` }} />
    </div>
  );
}

export default ProgressBar;
