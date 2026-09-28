import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { InlineText, balanceEmphasis } from "./inline-text";

afterEach(cleanup);

describe("InlineText", () => {
  it("renders LaTeX and emphasis from course material inline", () => {
    const { container } = render(
      <p>
        <InlineText text="On rejette $H_0$ si **p ≤ α** : [lien](http://x) ![img](x.png)" />
      </p>,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector("strong")?.textContent).toBe("p ≤ α");
    expect(container.querySelector("a, img, p p")).toBeNull();
    expect(container.textContent).toContain("lien");
  });

  it("drops unmatched bold markers from quotes cut mid-emphasis", () => {
    expect(balanceEmphasis("Règle de décision** : si p ≤ α")).toBe("Règle de décision : si p ≤ α");
    expect(balanceEmphasis("**rejeter H0** ici")).toBe("**rejeter H0** ici");
  });
});
