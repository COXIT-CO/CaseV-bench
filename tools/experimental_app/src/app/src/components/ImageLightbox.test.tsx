import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { ImageLightbox, type LightboxImage } from "@/components/ImageLightbox";

const IMAGES: LightboxImage[] = [
  { src: "/img/1.png", label: "Page 1" },
  { src: "/img/2.png", label: "Page 2" },
  { src: "/img/3.png", label: "Page 3" },
];

// A harness that owns the open-index the same way a real consumer (ResultDetail /
// DrawingDetail) does — null means closed, a number opens the viewer at that image.
function Harness({ start }: { start: number | null }) {
  const [index, setIndex] = React.useState<number | null>(start);
  return (
    <ImageLightbox
      images={IMAGES}
      index={index}
      onIndexChange={setIndex}
      onClose={() => setIndex(null)}
    />
  );
}

describe("ImageLightbox", () => {
  it("renders nothing while closed", () => {
    render(<Harness start={null} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens at the given start image with a page counter", () => {
    render(<Harness start={1} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/img/2.png");
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("moves next/prev with the counter and stops at the ends", async () => {
    const user = userEvent.setup();
    render(<Harness start={0} />);

    // At the first image the prev control is disabled; next advances the counter.
    expect(screen.getByRole("button", { name: /previous image/i })).toBeDisabled();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/img/1.png");

    await user.click(screen.getByRole("button", { name: /next image/i }));
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/img/2.png");

    await user.click(screen.getByRole("button", { name: /next image/i }));
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/img/3.png");

    // At the last image the next control is disabled (stop-at-ends, no wrap).
    expect(screen.getByRole("button", { name: /next image/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /previous image/i }));
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("navigates with the Left/Right arrow keys", () => {
    render(<Harness start={0} />);
    const dialog = screen.getByRole("dialog");

    fireEvent.keyDown(dialog, { key: "ArrowRight" });
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "/img/2.png");

    fireEvent.keyDown(dialog, { key: "ArrowLeft" });
    expect(screen.getByText("1 / 3")).toBeInTheDocument();

    // Left at the first image is a no-op (does not wrap).
    fireEvent.keyDown(dialog, { key: "ArrowLeft" });
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
  });

  it("closes on Esc", async () => {
    const user = userEvent.setup();
    render(<Harness start={0} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("zooms on wheel and the fit control returns to fit", async () => {
    const user = userEvent.setup();
    render(<Harness start={0} />);
    const img = screen.getByRole("img");

    // At fit the image is drawn at scale 1.
    expect(img.style.transform).toContain("scale(1)");

    // Wheeling up zooms in past fit.
    fireEvent.wheel(img, { deltaY: -100 });
    expect(img.style.transform).not.toContain("scale(1)");

    // The fit/reset control returns to scale 1.
    await user.click(screen.getByRole("button", { name: /reset zoom/i }));
    expect(img.style.transform).toContain("scale(1)");
  });
});
