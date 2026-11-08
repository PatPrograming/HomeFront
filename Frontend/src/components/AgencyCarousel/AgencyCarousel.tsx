// Thin blue marquee of federal agency seals, shown right below the navbar.
import usda from "../../assets/agency-logos/usda.png";
import commerce from "../../assets/agency-logos/commerce.png";
import defense from "../../assets/agency-logos/defense.png";
import education from "../../assets/agency-logos/education.png";
import energy from "../../assets/agency-logos/energy.png";
import epa from "../../assets/agency-logos/epa.png";
import gsa from "../../assets/agency-logos/gsa.png";
import hhs from "../../assets/agency-logos/hhs.png";
import dhs from "../../assets/agency-logos/dhs.png";
import hud from "../../assets/agency-logos/hud.png";
import interior from "../../assets/agency-logos/interior.png";
import justice from "../../assets/agency-logos/justice.png";
import labor from "../../assets/agency-logos/labor.png";
import nasa from "../../assets/agency-logos/nasa.png";
import ncua from "../../assets/agency-logos/ncua.png";
import nsf from "../../assets/agency-logos/nsf.png";
import nrc from "../../assets/agency-logos/nrc.png";
import opm from "../../assets/agency-logos/opm.png";
import sba from "../../assets/agency-logos/sba.png";
import ssa from "../../assets/agency-logos/ssa.png";
import state from "../../assets/agency-logos/state.png";
import dot from "../../assets/agency-logos/dot.png";
import treasury from "../../assets/agency-logos/treasury.png";
import va from "../../assets/agency-logos/va.png";
import dfc from "../../assets/agency-logos/dfc.png";
import "./AgencyCarousel.css";

const AGENCIES = [
  { name: "Department of Agriculture", logo: usda },
  { name: "Department of Commerce", logo: commerce },
  { name: "Department of Defense", logo: defense },
  { name: "Department of Education", logo: education },
  { name: "Department of Energy", logo: energy },
  { name: "Environmental Protection Agency", logo: epa },
  { name: "General Services Administration", logo: gsa },
  { name: "Department of Health and Human Services", logo: hhs },
  { name: "Department of Homeland Security", logo: dhs },
  { name: "Department of Housing and Urban Development", logo: hud },
  { name: "Department of the Interior", logo: interior },
  { name: "Department of Justice", logo: justice },
  { name: "Department of Labor", logo: labor },
  { name: "NASA", logo: nasa },
  { name: "National Credit Union Administration", logo: ncua },
  { name: "National Science Foundation", logo: nsf },
  { name: "Nuclear Regulatory Commission", logo: nrc },
  { name: "Office of Personnel Management", logo: opm },
  { name: "Small Business Administration", logo: sba },
  { name: "Social Security Administration", logo: ssa },
  { name: "Department of State", logo: state },
  { name: "Department of Transportation", logo: dot },
  { name: "Department of the Treasury", logo: treasury },
  { name: "Department of Veterans Affairs", logo: va },
  { name: "Development Finance Corporation", logo: dfc },
];

export default function AgencyCarousel() {
  return (
    <section className="agency-carousel" aria-label="Federal agencies">
      <ul className="agency-track">
        {/* Rendered twice so the loop is seamless; the copy is hidden from assistive tech. */}
        {[0, 1].map((copy) =>
          AGENCIES.map((a) => (
            <li key={`${copy}-${a.name}`} aria-hidden={copy === 1 || undefined}>
              <img src={a.logo} alt={copy === 0 ? a.name : ""} title={a.name} loading="lazy" />
            </li>
          )),
        )}
      </ul>
    </section>
  );
}
