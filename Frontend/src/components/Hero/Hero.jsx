import "./Hero.css";

export default function Hero({ mission, buttons }) {
  return (
    <section className="hero">
      <div className="hero-text">
        <h1 className="mission">
          <span className="line-1">{mission[0]}</span>
          <span className="line-2">{mission[1]}</span>
        </h1>
        <div className="hero-buttons">
          <button className="btn btn-primary">{buttons[0]}</button>
          <button className="btn btn-outline">{buttons[1]}</button>
        </div>
      </div>

      <div className="demo-box">Image that shows demo</div>
    </section>
  );
}
