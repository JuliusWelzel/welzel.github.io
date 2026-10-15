/*
 * Animations for the website, built on Motion (https://motion.dev, MIT),
 * which is self-hosted in assets/js.
 *
 * A small script in the page header adds the class "motion-ready" to <html>
 * (unless the visitor prefers reduced motion), which hides the animated
 * elements via CSS until this script reveals them. If this script never
 * runs, the header script removes the class again after three seconds.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var M = window.Motion;
  // Without the Motion library the page runs in the "reduced motion" mode:
  // nothing is animated and nothing stays hidden, but the topic filter works.
  if (!M) root.classList.remove("motion-ready");
  window.__siteMotionStarted = true;

  var animate = M && M.animate, inView = M && M.inView, stagger = M && M.stagger;
  var reduce = !root.classList.contains("motion-ready");
  var ease = [0.22, 1, 0.36, 1];

  function $(sel, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(sel)); }

  function show(nodes) { nodes.forEach(function (n) { n.style.opacity = 1; }); }

  // Motion animates opacity and stroke properties of SVG elements as
  // attributes, so initial states inside figures are set as attributes too.
  function hideSvg(nodes) { nodes.forEach(function (n) { n.setAttribute("opacity", 0); }); }

  function belowFold(node) { return node.getBoundingClientRect().top > window.innerHeight; }

  // Prepare a path for a "drawing" animation.
  function prepareDraw(path) {
    var len = path.getTotalLength();
    path.setAttribute("stroke-dasharray", len + " " + len);
    path.setAttribute("stroke-dashoffset", reduce ? 0 : len);
    return len;
  }

  function draw(path, opts) {
    var len = prepareDraw(path);
    if (reduce) return Promise.resolve();
    return animate(path, { strokeDashoffset: [len, 0] },
                   Object.assign({ duration: 1.6, ease: "linear" }, opts || {}));
  }

  // Run fn once, when the element scrolls into view.
  function once(node, fn, amount) {
    var stop = inView(node, function () {
      if (stop) stop();
      fn();
    }, { amount: amount || 0.3 });
  }

  // -------------------------------------------------------------------------
  // Page entrance and scroll reveals

  function hero() {
    var parts = $(".hero > *");
    if (!parts.length) return;
    if (reduce) { show(parts); return; }
    animate(parts, { opacity: [0, 1], y: [18, 0] },
            { duration: 0.8, delay: stagger(0.12), ease: ease });
  }

  // Groups whose children are hidden by CSS until they scroll into view.
  function groups() {
    $(".card-grid, .home-grid, .facts-group, .logo-strip, .checklist").forEach(function (group) {
      var items = group.classList.contains("checklist") ? $("li", group) : $(":scope > *", group);
      if (reduce) { show(items); return; }
      once(group, function () {
        animate(items, { opacity: [0, 1], y: [22, 0] },
                { duration: 0.7, delay: stagger(0.08), ease: ease });
      }, 0.1);
    });
    $(".reveal").forEach(function (node) {
      if (reduce) { show([node]); return; }
      once(node, function () {
        animate(node, { opacity: [0, 1], y: [24, 0] }, { duration: 0.8, ease: ease });
      }, 0.2);
    });
  }

  // Long lists: only entries below the fold fade in as they appear.
  function lists() {
    if (reduce) return;
    $(".pub-list li").forEach(function (item) {
      if (!belowFold(item)) return;
      item.style.opacity = 0;
      once(item, function () {
        animate(item, { opacity: [0, 1], y: [14, 0] }, { duration: 0.55, ease: ease });
      }, 0.2);
    });
  }

  // -------------------------------------------------------------------------
  // Topic filter on the projects page

  function filters() {
    var buttons = $(".chip[data-filter]");
    if (!buttons.length) return;
    var cards = $(".project-card[data-topics]");
    $(".chip-row").forEach(function (row) { row.hidden = false; });

    // Hide a section such as "Earlier projects" when the filter leaves it empty.
    function updateSections() {
      $(".card-grid").forEach(function (grid) {
        var section = grid.closest("section.level2");
        if (section) section.hidden = !grid.querySelector(".project-card:not([hidden])");
      });
    }
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var topic = btn.getAttribute("data-filter");
        buttons.forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
        var keep = [], drop = [];
        cards.forEach(function (card) {
          var topics = " " + card.getAttribute("data-topics") + " ";
          (topic === "all" || topics.indexOf(" " + topic + " ") >= 0 ? keep : drop).push(card);
        });
        if (reduce) {
          drop.forEach(function (c) { c.hidden = true; });
          keep.forEach(function (c) { c.hidden = false; c.style.opacity = 1; });
          updateSections();
          return;
        }
        var leaving = drop.filter(function (c) { return !c.hidden; });
        var out = leaving.length
          ? animate(leaving, { opacity: 0, scale: 0.97 }, { duration: 0.2 })
          : Promise.resolve();
        out.then(function () {
          drop.forEach(function (c) { c.hidden = true; });
          keep.forEach(function (c) { c.hidden = false; });
          updateSections();
          animate(keep, { opacity: [0, 1], scale: [0.97, 1], y: [12, 0] },
                  { duration: 0.45, delay: stagger(0.05), ease: ease });
        });
      });
    });
  }

  // -------------------------------------------------------------------------
  // Figures (inline SVG with data-anim="...")

  // Synchronised streams (EEG, EMG, motion) with heel-strike markers.
  function figStreams(svg) {
    var traces = $(".trace", svg);
    var marks = $(".hs", svg);
    traces.forEach(prepareDraw);
    if (reduce) return;
    hideSvg(marks);
    once(svg, function () {
      traces.forEach(function (t) { draw(t, { duration: 3.2 }); });
      animate(marks, { opacity: [0, 1] },
              { duration: 0.3, delay: stagger(3.2 / Math.max(marks.length, 1), { startDelay: 0.3 }) });
    });
  }

  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  // A walking person with EEG cap and motion sensors; the signals become time
  // series, which are then stored in a BIDS folder structure.
  function figPipeline(svg) {
    var limbs = $(".limb", svg);
    var flows = $(".flow", svg);
    var traces = $(".trace", svg);
    var arrow = $(".bids-arrow", svg);
    var rows = $(".bids-row", svg);
    var swing = { "arm-front": 22, "arm-back": -22, "leg-front": -26, "leg-back": 26 };
    function pose(phase) {
      limbs.forEach(function (limb) {
        var angle = swing[limb.classList[1]] * Math.sin(phase);
        limb.setAttribute("transform", "rotate(" + angle.toFixed(1) + " " +
                          limb.getAttribute("data-px") + " " + limb.getAttribute("data-py") + ")");
      });
    }
    function dashed() { flows.forEach(function (f) { f.setAttribute("stroke-dasharray", "3 4"); f.setAttribute("stroke-dashoffset", 0); }); }
    pose(0.7);
    traces.forEach(prepareDraw);
    if (reduce) { dashed(); return; }
    flows.forEach(prepareDraw);
    hideSvg(arrow.concat(rows));
    animate(0, 2 * Math.PI, { duration: 1.3, repeat: Infinity, ease: "linear", onUpdate: pose });
    once(svg, function () {
      var run = function () {
        hideSvg(arrow.concat(rows));
        flows.concat(traces).forEach(prepareDraw);
        Promise.all(flows.map(function (f) { return draw(f, { duration: 0.9, ease: "easeOut" }); }))
          .then(function () {
            dashed();
            return Promise.all(traces.map(function (t) { return draw(t, { duration: 2.2 }); }));
          })
          .then(function () { return animate(arrow, { opacity: [0, 1] }, { duration: 0.4 }); })
          .then(function () { return animate(rows, { opacity: [0, 1] }, { duration: 0.35, delay: stagger(0.16) }); })
          .then(function () { return wait(4500); })
          .then(run);
      };
      run();
    });
  }

  // Forking paths: every combination of preprocessing choices leads from the
  // same raw EEG to a different result; the reported path is highlighted.
  function figForking(svg) {
    var forks = $(".fork", svg);
    var reported = svg.querySelector(".fork-reported");
    var nodes = $(".node", svg);
    var outcomes = $(".outcome", svg);
    var tag = $(".reported-label", svg);
    var others = forks.filter(function (f) { return f !== reported; });
    function highlight() {
      reported.setAttribute("stroke", "#b3261e");
      reported.setAttribute("stroke-width", "2.4");
      reported.setAttribute("stroke-opacity", "1");
    }
    forks.forEach(prepareDraw);
    if (reduce) { highlight(); others.forEach(function (f) { f.setAttribute("stroke-opacity", "0.25"); }); return; }
    hideSvg(nodes.concat(outcomes, tag));
    once(svg, function () {
      animate(nodes, { opacity: [0, 1] }, { duration: 0.4, delay: stagger(0.08) });
      Promise.all(forks.map(function (f, i) { return draw(f, { duration: 1.3, delay: 0.4 + i * 0.12, ease: "easeInOut" }); }))
        .then(function () { return animate(outcomes, { opacity: [0, 1] }, { duration: 0.3, delay: stagger(0.06) }); })
        .then(function () {
          highlight();
          animate(others, { strokeOpacity: 0.25 }, { duration: 0.8 });
          return animate(tag, { opacity: [0, 1] }, { duration: 0.6 });
        });
    });
  }

  // EEGManySteps: new recordings with a shared protocol and existing datasets
  // flow into one harmonised format, which serves shared research questions.
  function figManysteps(svg) {
    var sources = $(".source", svg);
    var flows = $(".flow-path", svg);
    var hub = $(".hub", svg);
    var questions = $(".question", svg);
    flows.forEach(prepareDraw);
    if (reduce) return;
    hideSvg(sources.concat(hub, questions));
    function packets() {
      flows.forEach(function (f, i) {
        var len = f.getTotalLength();
        var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("r", 3);
        dot.setAttribute("fill", f.getAttribute("stroke"));
        svg.insertBefore(dot, hub[0]);
        animate(0, 1, {
          duration: 1.8, delay: i * 0.3, repeat: Infinity, repeatDelay: 1.2, ease: "easeInOut",
          onUpdate: function (v) {
            var pt = f.getPointAtLength(v * len);
            dot.setAttribute("cx", pt.x.toFixed(1));
            dot.setAttribute("cy", pt.y.toFixed(1));
          },
        });
      });
    }
    once(svg, function () {
      animate(sources, { opacity: [0, 1], x: [-12, 0] }, { duration: 0.45, delay: stagger(0.14), ease: ease })
        .then(function () { return Promise.all(flows.map(function (f, i) { return draw(f, { duration: 0.9, delay: i * 0.1, ease: "easeOut" }); })); })
        .then(function () { return animate(hub, { opacity: [0, 1] }, { duration: 0.5 }); })
        .then(function () { return animate(questions, { opacity: [0, 1], x: [10, 0] }, { duration: 0.45, delay: stagger(0.22), ease: ease }); })
        .then(packets);
    });
  }

  // Bayesian cue integration: prior x likelihood = posterior. The foot is
  // placed at the posterior mean while the sensory noise changes.
  function figBayes(svg) {
    var base = 200, left = 40, right = 600, peak = 150;
    var prior = { mu: 0.38, sd: 0.13 };
    var likMu = 0.66;
    var pPrior = svg.querySelector(".prior");
    var pLik = svg.querySelector(".likelihood");
    var pPost = svg.querySelector(".posterior");
    var foot = svg.querySelector(".foot");
    var label = svg.querySelector(".noise-label");

    function gauss(x, mu, sd) { return Math.exp(-0.5 * Math.pow((x - mu) / sd, 2)); }
    function curve(mu, sd, area, height) {
      var d = "", n = 120;
      for (var i = 0; i <= n; i++) {
        var x = i / n;
        d += (i ? "L" : "M") + (left + x * (right - left)).toFixed(1) + "," +
             (base - peak * height * gauss(x, mu, sd)).toFixed(1);
      }
      if (area) d += "L" + right + "," + base + "L" + left + "," + base + "Z";
      return d;
    }
    function render(sdL) {
      var wP = 1 / (prior.sd * prior.sd), wL = 1 / (sdL * sdL);
      var mu = (prior.mu * wP + likMu * wL) / (wP + wL);
      var sd = Math.sqrt(1 / (wP + wL));
      // Peak heights follow the densities, so sharper curves stand taller.
      pPrior.setAttribute("d", curve(prior.mu, prior.sd, false, 0.055 / prior.sd));
      pLik.setAttribute("d", curve(likMu, sdL, false, Math.min(1, 0.055 / sdL)));
      pPost.setAttribute("d", curve(mu, sd, true, Math.min(1.05, 0.055 / sd)));
      if (foot) foot.setAttribute("transform", "translate(" + (left + mu * (right - left)).toFixed(1) + "," + (base + 30) + ")");
      if (label) label.textContent = sdL < 0.1
        ? "Reliable sensory estimate: the evidence dominates"
        : "Noisy sensory estimate: prior expectations take over";
    }
    render(0.07);
    if (reduce) return;
    once(svg, function () {
      var loop = function () {
        animate(0.07, 0.24, { duration: 2.6, ease: "easeInOut", delay: 1, onUpdate: render })
          .then(function () { return animate(0.24, 0.07, { duration: 2.6, ease: "easeInOut", delay: 1.4, onUpdate: render }); })
          .then(loop);
      };
      loop();
    });
  }

  // A community network forming around a shared core.
  function figNetwork(svg) {
    var edges = $(".edge", svg);
    var nodes = $(".node", svg);
    edges.forEach(prepareDraw);
    if (reduce) return;
    hideSvg(nodes);
    once(svg, function () {
      animate(nodes, { opacity: [0, 1], scale: [0, 1] }, { duration: 0.45, delay: stagger(0.06), ease: ease });
      edges.forEach(function (e, i) { draw(e, { duration: 0.7, delay: 0.5 + i * 0.07, ease: "easeOut" }); });
    });
  }

  var FIGURES = {
    pipeline: figPipeline,
    streams: figStreams,
    forking: figForking,
    manysteps: figManysteps,
    bayes: figBayes,
    network: figNetwork,
  };

  function figures() {
    $("svg[data-anim]").forEach(function (svg) {
      var fn = FIGURES[svg.getAttribute("data-anim")];
      try { if (fn) fn(svg); } catch (err) { if (window.console) console.warn("animation failed", err); }
      svg.style.opacity = 1;
    });
  }

  function init() {
    hero();
    groups();
    lists();
    filters();
    figures();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
