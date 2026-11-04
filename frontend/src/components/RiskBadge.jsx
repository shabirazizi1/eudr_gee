export default function RiskBadge({ level }) {
  if (!level) return <span className="badge none">Not screened</span>
  return <span className={`badge ${level.toLowerCase()}`}>● {level} risk</span>
}
