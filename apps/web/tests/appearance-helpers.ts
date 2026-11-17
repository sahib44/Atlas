import { expect, type Locator, type Page } from "@playwright/test";

export async function selectAppearance(page: Page, theme: "Light" | "Dark" | "Purple Dark") {
  await page.getByRole("button", { name: "Appearance", exact: true }).click();
  await page.getByRole("group", { name: "Theme", exact: true })
    .getByRole("button", { name: theme, exact: true }).click();
}

export async function expectReadable(locator: Locator, placeholder = false) {
  await expect(locator).toBeVisible();
  const contrast = await locator.evaluate((element, checkPlaceholder) => {
    function rgba(color: string) {
      return color.match(/[\d.]+/g)?.map(Number) ?? [];
    }
    function luminance(color: number[]) {
      const [red, green, blue] = color.slice(0, 3).map(component => {
        const channel = component / 255;
        return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    }
    const foreground = rgba(getComputedStyle(element, checkPlaceholder ? "::placeholder" : null).color);
    for (let current: Element | null = element; current; current = current.parentElement) {
      const background = rgba(getComputedStyle(current).backgroundColor);
      if (background.length === 3 || background[3] === 1) {
        const light = luminance(foreground);
        const dark = luminance(background);
        return (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05);
      }
    }
    throw new Error("Expected an opaque theme surface");
  }, placeholder);
  expect(contrast, locator.toString()).toBeGreaterThanOrEqual(4.5);
}
