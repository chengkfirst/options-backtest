/* core.js — pure computation and formatting; it never touches the DOM
 *
 * This layer does two things: turn numbers into strings a person reads
 * (money / pct / fmtVal), and turn trade rows into statistics
 * (stats / quantile / bucketize). There is not one reference to `document` in the
 * file, so it can be tested on its own and reused elsewhere.
 *
 * To add a statistic, or change how a column is displayed, change it here.
 */
"use strict";

const LABEL={entry_iv:"IV at entry",entry_delta:"delta at entry",
 entry_moneyness:"moneyness at entry",entry_dte_calendar:"days to expiry at entry",
 entry_underlying_price:"spot at entry",entry_price:"premium paid",
 exit_price:"premium received",holding_days:"trading days held",
 entry_sig_rsi_14:"RSI at entry",entry_sig_rv_20:"realised vol at entry",
 entry_sig_mom_20:"20-day momentum at entry",entry_sig_atr_14:"ATR at entry",
 entry_sig_bb_width_20:"Bollinger width at entry",ma_gap:"price vs the 20-day",
 trend:"price vs the 200-day",exit_iv:"IV at exit",exit_delta:"delta at exit"};
const PCT=new Set(["entry_moneyness","ma_gap","trend","entry_sig_mom_20","entry_iv","exit_iv",
 "entry_sig_rv_20","entry_sig_bb_width_20"]);
const REASON={take_profit:"take profit",stop_loss:"stop loss",time_exit:"holding cap",
 dte_floor:"days-to-expiry floor",expiry_itm:"expired in the money",
 expiry_otm:"expired out of the money",risk_forced:"forced by risk"};
const EXCLUDE=new Set(["trade_id","gross_pnl","fees","net_pnl","entry_sig_close",
 "entry_sig_sma_20","entry_sig_sma_50","entry_sig_sma_200"]);

/* Absence prints as "—", never as a number.
 *
 * The backend sends `null` for "this cannot be computed" (a Sharpe on one return, a
 * drawdown with no equity curve) — `_finite` in runner.py converts every NaN and
 * infinity to null for exactly that reason, and its comment promises that this side
 * shows "—" for null and keeps 0 for a real zero. It did not: `pct(null)` multiplied
 * null by 100 and printed **0.0%**, inventing the figure the conversion existed to
 * avoid, and `money(null)` printed "+0". A number that was never computed must not
 * appear as one. */
const absent=v=>v==null||(typeof v==="number"&&!Number.isFinite(v));
const money=v=>absent(v)?"—":(v>=0?"+":"−")+Math.abs(v).toLocaleString("en-US",{maximumFractionDigits:0});
const pct=v=>absent(v)?"—":(v*100).toFixed(1)+"%";
const num=(v,digits=2)=>absent(v)?"—":v.toFixed(digits);
const sgn=v=>absent(v)?"":v>0?"pos":v<0?"neg":"";
const label=c=>LABEL[c]||c;
function fmtVal(col,v){
  if(v==null||Number.isNaN(v))return"—";
  if(PCT.has(col))return(v*100).toFixed(1)+"%";
  if(Math.abs(v)>=100)return v.toFixed(0);
  if(Math.abs(v)>=1)return v.toFixed(2);
  return v.toFixed(3);
}
function quantile(s,q){if(!s.length)return NaN;const p=(s.length-1)*q,lo=Math.floor(p),hi=Math.ceil(p);
  return lo===hi?s[lo]:s[lo]+(s[hi]-s[lo])*(p-lo);}
function stats(rows){
  const p=rows.map(r=>r.net_pnl).sort((a,b)=>a-b),n=p.length;
  if(!n)return{n:0,sum:0,mean:0,median:0,win:0,min:0,max:0};
  const sum=p.reduce((a,b)=>a+b,0);
  return{n,sum,mean:sum/n,median:quantile(p,.5),win:p.filter(v=>v>0).length/n,min:p[0],max:p[n-1]};
}
function bucketize(rows,col,k){
  const vals=rows.map(r=>r[col]).filter(v=>v!=null&&!Number.isNaN(v)).sort((a,b)=>a-b);
  if(vals.length<k*2)return null;
  const edges=[vals[0]];
  for(let i=1;i<k;i++){const e=quantile(vals,i/k);if(e>edges[edges.length-1])edges.push(e);}
  edges.push(vals[vals.length-1]+1e-12);
  if(edges.length<3)return null;
  const g=edges.slice(0,-1).map((lo,i)=>({lo,hi:edges[i+1],rows:[]}));
  for(const r of rows){const v=r[col];if(v==null||Number.isNaN(v))continue;
    let i=g.findIndex(x=>v>=x.lo&&v<x.hi);if(i<0)i=g.length-1;g[i].rows.push(r);}
  return g.filter(x=>x.rows.length);
}
