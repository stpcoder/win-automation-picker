document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".md-content img").forEach((image) => {
    if (image.closest("a")) return;

    const link = document.createElement("a");
    link.href = image.currentSrc || image.src;
    link.target = "_blank";
    link.rel = "noopener";
    link.className = "manual-image-link";
    link.setAttribute("aria-label", `${image.alt || "화면"} 원본 크기로 보기`);
    image.parentNode.insertBefore(link, image);
    link.appendChild(image);
  });
});
