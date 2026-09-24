import { ReadinessPanel } from "../components/readiness-panel";

export default function Home() {
  return (
    <main className="page">
      <section className="card" aria-labelledby="title">
        <p className="eyebrow">Local development</p>
        <h1 id="title">Course assistant</h1>
        <p className="intro">The application scaffold is running. API connectivity is checked below.</p>
        <ReadinessPanel />
      </section>
    </main>
  );
}
