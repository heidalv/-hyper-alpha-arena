import{j as a}from"./index-637008ab.js";function n({className:t="w-8 h-4"}){return a.jsxs("svg",{xmlns:"http://www.w3.org/2000/svg",version:"1.1",className:t,viewBox:"50 0 500 300",style:{color:"currentColor"},children:[a.jsx("style",{children:`
        .pacman-dot {
          fill: currentColor;
        }
        .pacman-open,
        .pacman-mouth-top,
        .pacman-mouth-bottom {
          fill: currentColor;
        }
        .pacman-mouth-top,
        .pacman-mouth-bottom {
          animation-duration: 175ms;
          animation-timing-function: linear;
          animation-direction: alternate;
          animation-iteration-count: infinite;
          transform-origin: 150px 150px;
        }
        .pacman-mouth-top {
          animation-name: rotate-counterclockwise;
        }
        .pacman-mouth-bottom {
          animation-name: rotate-clockwise;
        }
        @keyframes rotate-counterclockwise {
          100% { transform: rotate(-30deg); }
        }
        @keyframes rotate-clockwise {
          100% { transform: rotate(30deg); }
        }
        .pacman-dot {
          animation-name: dot-motion;
          animation-duration: 600ms;
          animation-timing-function: linear;
          animation-iteration-count: infinite;
        }
        @keyframes dot-motion {
          100% { transform: translateX(-100px); }
        }
      `}),a.jsx("circle",{className:"pacman-dot",cx:"250",cy:"50%",r:"10"}),a.jsx("circle",{className:"pacman-dot",cx:"350",cy:"50%",r:"10"}),a.jsx("circle",{className:"pacman-dot",cx:"450",cy:"50%",r:"10"}),a.jsx("circle",{className:"pacman-dot",cx:"550",cy:"50%",r:"10"}),a.jsx("circle",{className:"pacman-dot",cx:"650",cy:"50%",r:"10"}),a.jsx("path",{className:"pacman-mouth-bottom",d:"M 150,150 L 220.4,221.0 A 100 100 0 0 0 250,150 Z"}),a.jsx("path",{className:"pacman-mouth-top",d:"M 150,150 L 220.4,79.0 A 100 100 0 0 1 250,150 Z"}),a.jsx("path",{className:"pacman-open",d:"M 150,150 L 236.6,100 A 100 100 0 1 0 236.6,200 Z"})]})}export{n as P};
