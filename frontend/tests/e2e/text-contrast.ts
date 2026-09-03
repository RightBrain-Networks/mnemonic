import type { Locator, Page } from "@playwright/test";

export type TextContrastFailure = {
  background: string;
  contrast: number;
  foreground: string;
  issue: "below 4.5:1" | "above 7:1";
  selector: string;
  text: string;
};

/** Audit rendered text against the dark theme's low-glare 4.5:1–7:1 target band. */
export async function auditTextContrast(
  page: Page,
  root?: Locator
): Promise<TextContrastFailure[]> {
  const scope = root ?? page.locator("body");
  return await scope.evaluate((scopeElement) => {
    type Rgba = [number, number, number, number];

    function rgba(value: string): Rgba | null {
      const match = value.match(/^rgba?\((\d+(?:\.\d+)?)\s*[ ,]\s*(\d+(?:\.\d+)?)\s*[ ,]\s*(\d+(?:\.\d+)?)(?:\s*[,/]\s*(\d+(?:\.\d+)?))?\)$/);
      if (!match) return null;
      return [Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4] ?? 1)];
    }

    function over(foreground: Rgba, background: Rgba): Rgba {
      const alpha = foreground[3] + background[3] * (1 - foreground[3]);
      if (alpha === 0) return [0, 0, 0, 0];
      return [
        (foreground[0] * foreground[3]
          + background[0] * background[3] * (1 - foreground[3])) / alpha,
        (foreground[1] * foreground[3]
          + background[1] * background[3] * (1 - foreground[3])) / alpha,
        (foreground[2] * foreground[3]
          + background[2] * background[3] * (1 - foreground[3])) / alpha,
        alpha
      ];
    }

    function effectiveBackground(element: Element): Rgba {
      let result: Rgba = [0, 0, 0, 0];
      for (let current: Element | null = element; current; current = current.parentElement) {
        const color = rgba(getComputedStyle(current).backgroundColor);
        if (color) result = over(result, color);
        if (result[3] >= .999) return result;
      }
      return over(result, [255, 255, 255, 1]);
    }

    function luminance(color: Rgba): number {
      const channels = color.slice(0, 3).map((channel) => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      });
      return .2126 * channels[0]! + .7152 * channels[1]! + .0722 * channels[2]!;
    }

    function contrast(first: Rgba, second: Rgba): number {
      const firstLuminance = luminance(first);
      const secondLuminance = luminance(second);
      return (Math.max(firstLuminance, secondLuminance) + .05)
        / (Math.min(firstLuminance, secondLuminance) + .05);
    }

    function selector(element: Element): string {
      const classes = [...element.classList].slice(0, 3).join(".");
      return `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}`
        + `${classes ? `.${classes}` : ""}`;
    }

    const failures: TextContrastFailure[] = [];
    for (const element of scopeElement.querySelectorAll("*")) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (
        rect.width === 0
        || rect.height === 0
        || style.display === "none"
        || style.visibility !== "visible"
        || Number(style.opacity) === 0
        || element.closest("[aria-hidden='true']")
        || (element instanceof HTMLButtonElement && element.disabled)
        || (element instanceof HTMLInputElement && element.disabled)
        || (element instanceof HTMLSelectElement && element.disabled)
        || (element instanceof HTMLTextAreaElement && element.disabled)
      ) continue;

      const directText = [...element.childNodes]
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent?.trim() ?? "")
        .filter(Boolean)
        .join(" ");
      const nonTextInputTypes = new Set([
        "checkbox", "color", "file", "hidden", "radio", "range"
      ]);
      let controlText = "";
      let placeholder = "";
      if (element instanceof HTMLInputElement && !nonTextInputTypes.has(element.type)) {
        placeholder = !element.value ? element.getAttribute("placeholder") ?? "" : "";
        controlText = element.value || placeholder;
      } else if (element instanceof HTMLTextAreaElement) {
        placeholder = !element.value ? element.getAttribute("placeholder") ?? "" : "";
        controlText = element.value || placeholder;
      } else if (element instanceof HTMLSelectElement) {
        controlText = element.selectedOptions[0]?.textContent?.trim() ?? "";
      }
      const text = directText || controlText;
      if (!text) continue;

      const textStyle = placeholder ? getComputedStyle(element, "::placeholder") : style;
      const foreground = rgba(textStyle.color);
      const background = effectiveBackground(element);
      if (!foreground) continue;
      const renderedForeground = over(foreground, background);
      const ratio = contrast(renderedForeground, background);
      const issue = ratio + Number.EPSILON < 4.5
        ? "below 4.5:1"
        : ratio - Number.EPSILON > 7
          ? "above 7:1"
          : null;
      if (issue) failures.push({
        background: `rgb(${background.slice(0, 3).map(Math.round).join(", ")})`,
        contrast: Number(ratio.toFixed(2)),
        foreground: textStyle.color,
        issue,
        selector: selector(element),
        text: text.slice(0, 80)
      });
    }
    return failures;
  });
}
