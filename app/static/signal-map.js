/* Signal map charts — port of ms.bars / ms.months from docs/design/signal_map_dashboard.html */
(() => {
  const ms = {
    fmt: (n) => (window.d3 ? d3.format(",")(n) : String(n)),
    COL: {
      tightening: {
        supplier: "var(--tight)",
        buyer: "var(--tight-2)",
      },
      easing: {
        supplier: "var(--ease)",
        buyer: "var(--ease-2)",
      },
    },
    label: (f) => String(f || "").replace(/_/g, " "),
    tip: (el) => {
      let t = el.querySelector(".ms-tip");
      if (!t) {
        t = document.createElement("div");
        t.className = "ms-tip";
        t.hidden = true;
        el.appendChild(t);
      }
      return {
        show: (event, head, lines) => {
          t.replaceChildren();
          const h = document.createElement("div");
          h.className = "h";
          h.textContent = head;
          t.appendChild(h);
          for (const [color, text] of lines) {
            const l = document.createElement("div");
            l.className = "l";
            const i = document.createElement("i");
            i.style.background = color;
            const s = document.createElement("span");
            s.textContent = text;
            l.append(i, s);
            t.appendChild(l);
          }
          t.hidden = false;
          const box = el.getBoundingClientRect();
          const x = event.clientX - box.left;
          const y = event.clientY - box.top;
          const w = t.offsetWidth;
          const hgt = t.offsetHeight;
          t.style.left = Math.max(0, Math.min(box.width - w, x + 12)) + "px";
          t.style.top = Math.max(0, y - hgt - 10) + "px";
        },
        hide: () => {
          t.hidden = true;
        },
      };
    },
  };

  // Diverging horizontal bars: easing left of zero, tightening right; supplier then buyer.
  ms.bars = (el, rows, opts = {}) => {
    const d3 = window.d3;
    if (!d3 || !el) return;
    const W = el.clientWidth;
    if (!W) return;
    el.replaceChildren();
    const rowH = 30;
    const barH = 16;
    const top = 24;
    const H = top + rows.length * rowH + 4;
    const svg = d3
      .select(el)
      .append("svg")
      .attr("width", "100%")
      .attr("height", H)
      .attr("viewBox", `0 0 ${W} ${H}`);
    const measure = (texts, cls) => {
      const g = svg.append("g");
      let w = 0;
      g.selectAll("text")
        .data(texts)
        .join("text")
        .attr("class", cls)
        .text((d) => d)
        .each(function () {
          w = Math.max(w, this.getComputedTextLength());
        });
      g.remove();
      return w;
    };
    const labW = measure(
      rows.map((d) => d.label),
      "ms-lab"
    );
    const subW = measure(
      rows.map((d) => d.sub || ""),
      "ms-tick"
    );
    const left = Math.max(labW, subW) + 12;
    const sum = (o) => o.supplier + o.buyer;
    const maxT = Math.max(1, d3.max(rows, (d) => sum(d.tightening)) || 0);
    const maxE = Math.max(1, d3.max(rows, (d) => sum(d.easing)) || 0);
    const numW = measure([ms.fmt(Math.max(maxT, maxE))], "ms-val") + 8;
    const x = d3.scaleLinear([-maxE, maxT], [left + numW, W - numW]);
    const tip = ms.tip(el);

    svg
      .append("text")
      .attr("class", "ms-tick")
      .attr("x", x(0) - 6)
      .attr("y", 12)
      .attr("text-anchor", "end")
      .text("← Easing");
    svg
      .append("text")
      .attr("class", "ms-tick")
      .attr("x", x(0) + 6)
      .attr("y", 12)
      .text("Tightening →");

    const g = svg
      .selectAll("g.row")
      .data(rows)
      .join("g")
      .attr("class", "row")
      .attr("transform", (d, i) => `translate(0,${top + i * rowH})`)
      .attr("opacity", (d) =>
        opts.selected && opts.selected !== d.key ? 0.35 : 1
      );

    g.append("text")
      .attr("class", "ms-lab")
      .attr("x", 0)
      .attr("y", (d) => (d.sub ? rowH / 2 - 3 : rowH / 2 + 4))
      .text((d) => d.label);
    g.filter((d) => d.sub)
      .append("text")
      .attr("class", "ms-tick")
      .attr("x", 0)
      .attr("y", rowH / 2 + 10)
      .text((d) => d.sub);

    const segs = (d) => {
      const out = [];
      const yb = (rowH - barH) / 2;
      const ts = d.tightening.supplier;
      const tb = d.tightening.buyer;
      const es = d.easing.supplier;
      const eb = d.easing.buyer;
      if (ts)
        out.push({
          x0: x(0),
          x1: x(ts) - (tb ? 1 : 0),
          c: ms.COL.tightening.supplier,
          y: yb,
        });
      if (tb)
        out.push({
          x0: x(ts) + (ts ? 1 : 0),
          x1: x(ts + tb),
          c: ms.COL.tightening.buyer,
          y: yb,
        });
      if (es)
        out.push({
          x0: x(-es) + (eb ? 1 : 0),
          x1: x(0),
          c: ms.COL.easing.supplier,
          y: yb,
        });
      if (eb)
        out.push({
          x0: x(-es - eb),
          x1: x(-es) - (es ? 1 : 0),
          c: ms.COL.easing.buyer,
          y: yb,
        });
      return out;
    };
    g.selectAll("rect.seg")
      .data(segs)
      .join("rect")
      .attr("class", "seg")
      .attr("x", (s) => s.x0)
      .attr("width", (s) => Math.max(1, s.x1 - s.x0))
      .attr("y", (s) => s.y)
      .attr("height", barH)
      .attr("rx", 3)
      .style("fill", (s) => s.c);

    g.filter((d) => sum(d.tightening))
      .append("text")
      .attr("class", "ms-val")
      .attr("x", (d) => x(sum(d.tightening)) + 5)
      .attr("y", rowH / 2 + 4)
      .text((d) => ms.fmt(sum(d.tightening)));
    g.filter((d) => sum(d.easing))
      .append("text")
      .attr("class", "ms-val")
      .attr("text-anchor", "end")
      .attr("x", (d) => x(-sum(d.easing)) - 5)
      .attr("y", rowH / 2 + 4)
      .text((d) => ms.fmt(sum(d.easing)));

    svg
      .append("line")
      .attr("x1", x(0))
      .attr("x2", x(0))
      .attr("y1", top - 4)
      .attr("y2", H)
      .style("stroke", "var(--chart-baseline)")
      .attr("stroke-width", 1);

    g.append("rect")
      .attr("class", opts.onClick ? "ms-hit" : "")
      .attr("x", 0)
      .attr("width", W)
      .attr("height", rowH)
      .style("fill", "transparent")
      .on("mousemove", (event, d) =>
        tip.show(event, d.tipHead || d.label, [
          [
            ms.COL.tightening.supplier,
            `Tightening, supplier: ${ms.fmt(d.tightening.supplier)}`,
          ],
          [
            ms.COL.tightening.buyer,
            `Tightening, buyer: ${ms.fmt(d.tightening.buyer)}`,
          ],
          [
            ms.COL.easing.supplier,
            `Easing, supplier: ${ms.fmt(d.easing.supplier)}`,
          ],
          [
            ms.COL.easing.buyer,
            `Easing, buyer: ${ms.fmt(d.easing.buyer)}`,
          ],
        ])
      )
      .on("mouseleave", tip.hide)
      .on("click", (event, d) => opts.onClick && opts.onClick(d.key));
  };

  // Monthly columns: tightening up from baseline, easing down.
  ms.months = (el, months, counts) => {
    const d3 = window.d3;
    if (!d3 || !el) return;
    const W = el.clientWidth;
    if (!W) return;
    el.replaceChildren();
    const H = 220;
    const padT = 12;
    const padB = 36;
    const maxT = Math.max(
      1,
      d3.max(months, (m) => counts.get(m)?.tightening || 0)
    );
    const maxE = Math.max(
      1,
      d3.max(months, (m) => counts.get(m)?.easing || 0)
    );
    const y = d3.scaleLinear([-maxE, maxT], [H - padB, padT]).nice();
    const svg = d3
      .select(el)
      .append("svg")
      .attr("width", "100%")
      .attr("height", H)
      .attr("viewBox", `0 0 ${W} ${H}`);
    const ticks = y.ticks(4);
    const probe = svg.append("g");
    let tickW = 0;
    probe
      .selectAll("text")
      .data(ticks)
      .join("text")
      .attr("class", "ms-tick")
      .text((t) => ms.fmt(Math.abs(t)))
      .each(function () {
        tickW = Math.max(tickW, this.getComputedTextLength());
      });
    probe.remove();
    const left = tickW + 8;
    const x = d3.scaleBand(months, [left, W]).paddingInner(0.25).paddingOuter(0.1);
    const tip = ms.tip(el);

    svg
      .append("g")
      .selectAll("line")
      .data(ticks.filter((t) => t !== 0))
      .join("line")
      .attr("x1", left)
      .attr("x2", W)
      .attr("y1", (t) => y(t))
      .attr("y2", (t) => y(t))
      .style("stroke", "var(--chart-grid)")
      .attr("stroke-width", 1);
    svg
      .append("g")
      .selectAll("text")
      .data(ticks)
      .join("text")
      .attr("class", "ms-tick")
      .attr("x", left - 6)
      .attr("y", (t) => y(t) + 4)
      .attr("text-anchor", "end")
      .text((t) => ms.fmt(Math.abs(t)));

    const parse = (m) => new Date(m + "-01T00:00:00Z");
    const every = x.bandwidth() < 26 ? 2 : 1;
    svg
      .append("g")
      .selectAll("text")
      .data(months.filter((m, i) => i % every === 0))
      .join("text")
      .attr("class", "ms-tick")
      .attr("x", (m) => x(m) + x.bandwidth() / 2)
      .attr("y", H - padB + 16)
      .attr("text-anchor", "middle")
      .text((m) => d3.utcFormat("%b")(parse(m)));
    svg
      .append("g")
      .selectAll("text")
      .data(months.filter((m, i) => i === 0 || m.endsWith("-01")))
      .join("text")
      .attr("class", "ms-tick")
      .attr("x", (m) => x(m) + x.bandwidth() / 2)
      .attr("y", H - padB + 30)
      .attr("text-anchor", "middle")
      .text((m) => d3.utcFormat("%Y")(parse(m)));

    const g = svg.selectAll("g.col").data(months).join("g").attr("class", "col");
    const bw = Math.min(32, x.bandwidth());
    const bx = (m) => x(m) + (x.bandwidth() - bw) / 2;
    g.filter((m) => counts.get(m)?.tightening)
      .append("rect")
      .attr("x", bx)
      .attr("width", bw)
      .attr("y", (m) => y(counts.get(m).tightening))
      .attr("height", (m) => y(0) - y(counts.get(m).tightening) - 1)
      .attr("rx", 3)
      .style("fill", ms.COL.tightening.supplier);
    g.filter((m) => counts.get(m)?.easing)
      .append("rect")
      .attr("x", bx)
      .attr("width", bw)
      .attr("y", y(0) + 1)
      .attr("height", (m) => y(-counts.get(m).easing) - y(0) - 1)
      .attr("rx", 3)
      .style("fill", ms.COL.easing.supplier);
    svg
      .append("line")
      .attr("x1", left)
      .attr("x2", W)
      .attr("y1", y(0))
      .attr("y2", y(0))
      .style("stroke", "var(--chart-baseline)")
      .attr("stroke-width", 1);
    g.append("rect")
      .attr("x", (m) => x(m))
      .attr("width", x.bandwidth())
      .attr("y", padT)
      .attr("height", H - padT - padB)
      .style("fill", "transparent")
      .on("mousemove", (event, m) =>
        tip.show(event, d3.utcFormat("%B %Y")(parse(m)), [
          [
            ms.COL.tightening.supplier,
            `Tightening: ${ms.fmt(counts.get(m)?.tightening || 0)}`,
          ],
          [
            ms.COL.easing.supplier,
            `Easing: ${ms.fmt(counts.get(m)?.easing || 0)}`,
          ],
        ])
      )
      .on("mouseleave", tip.hide);
  };

  window.ms = ms;
})();
