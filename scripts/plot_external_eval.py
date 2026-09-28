#!/usr/bin/env python3
"""Render benchmark plots from published per-run scores; never synthesize results."""

import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.external.core import digest
from scripts.summarize_external_eval import aggregate, cluster_interval

EXT=ROOT/"evals/external"
RESULTS=EXT/"results/pilot-2026-09-29"
OUT=ROOT/"docs/assets"
COLORS={"without_skill":"#8996A8","with_skill":"#176B91"}
LABELS={"without_skill":"Without skill","with_skill":"With skill"}
CONDITIONS=list(COLORS)


def save(fig,name):
    OUT.mkdir(parents=True,exist_ok=True)
    fig.savefig(OUT/(name+".png"),dpi=180,facecolor="white")
    fig.savefig(OUT/(name+".svg"),metadata={"Date":None,"Creator":"agent-composition-review"},facecolor="white")
    svg=OUT/(name+".svg")
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
    plt.close(fig)


def main():
    manifest=json.loads((EXT/"pilot-manifest.json").read_text())
    rows=json.loads((RESULTS/"predictions.json").read_text())
    summary=json.loads((RESULTS/"summary.json").read_text())
    assert digest((RESULTS/"predictions.json").read_bytes())==summary["predictions_sha256"]
    assert len(rows)==summary["planned_runs"]==len(manifest["schedule"])
    assert aggregate(manifest,rows)==summary["datasets"],"Published summary does not match per-run scores"
    datasets=list(summary["datasets"])
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":10,"axes.spines.top":False,
                         "axes.spines.right":False,"svg.hashsalt":"agent-composition-review-pilot-20260929"})
    fig,axes=plt.subplots(1,len(datasets),figsize=(12.4,5.1),sharey=True)
    for ax,dataset in zip(np.atleast_1d(axes),datasets):
        stats=summary["datasets"][dataset]
        metrics=["location","agent","joint"]
        x=np.arange(len(metrics));width=.34
        for condition,shift in zip(CONDITIONS,[-width/2,width/2]):
            values=np.array([stats["metrics"][m][condition]["mean"] for m in metrics])*100
            cis=np.array([stats["metrics"][m][condition]["ci95"] for m in metrics])*100
            ax.bar(x+shift,values,width,color=COLORS[condition],label=LABELS[condition],zorder=2)
            ax.errorbar(x+shift,values,yerr=np.maximum(0,np.stack([values-cis[:,0],cis[:,1]-values])),
                        fmt="none",ecolor="#253246",capsize=3,lw=1,zorder=3)
            for xpos,val in zip(x+shift,values): ax.text(xpos,val/2,f"{val:.1f}",ha="center",va="center",color="white",fontsize=9,fontweight="bold")
        ax.set_xticks(x,[f"{label}\n(n={stats['metrics'][m]['independent_tasks']})"
                       for m,label in zip(metrics,["Exact location","Responsible agent","Joint attribution"])])
        ax.set_title(dataset+"\n12 unique tasks · 3 trials per condition",fontweight="bold",pad=13)
        ax.set_ylim(0,105);ax.set_yticks(range(0,101,20));ax.grid(axis="y",alpha=.18,zorder=0)
    np.atleast_1d(axes)[0].set_ylabel("Accuracy (%)")
    np.atleast_1d(axes)[-1].legend(loc="upper right",frameon=False,fontsize=9)
    fig.suptitle("External attribution pilot · GPT-6 Astra / high",fontsize=16,fontweight="bold",y=.98)
    fig.text(.5,.015,"144 planned runs; error bars: 95% task-cluster bootstrap. n counts tasks, not repeated runs.\nStratified pilot, not a full-benchmark score; AgentRx not evaluated.",ha="center",fontsize=9,color="#4B5563")
    fig.tight_layout(rect=[0,.105,1,.92]);save(fig,"external-pilot-accuracy")

    fig,ax=plt.subplots(figsize=(8,3.8))
    for y,dataset in enumerate(datasets):
        delta=summary["datasets"][dataset]["metrics"]["location"]["paired_difference"]
        value=delta["mean"]*100;lo,hi=np.array(delta["ci95"])*100
        ax.plot([lo,hi],[y,y],color=COLORS["with_skill"],lw=3)
        ax.plot(value,y,"o",color=COLORS["with_skill"],ms=8)
        ax.text(value,y+.16,f"{value:+.1f} pp  [{lo:+.1f}, {hi:+.1f}]",ha="center",fontsize=10)
    ax.axvline(0,color="#4B5563",ls="--",lw=1)
    ax.set_yticks(range(len(datasets)),datasets);ax.set_ylim(-.4,len(datasets)-.45)
    ax.set_xlabel("Exact-location accuracy: with skill minus without skill (percentage points)")
    ax.grid(axis="x",alpha=.18)
    ax.set_title("Paired differences · 95% task-cluster bootstrap",fontweight="bold",pad=16)
    fig.text(.5,.015,"12 tasks per dataset; 3 trials averaged per task. Stratified pilot, not a population guarantee.",ha="center",fontsize=8)
    fig.tight_layout(rect=[0,.07,1,1]);save(fig,"external-pilot-difference")

    fig,axes=plt.subplots(1,len(datasets),figsize=(12.4,4.8),sharey=True)
    for ax,dataset in zip(np.atleast_1d(axes),datasets):
        ids=[c["case_id"] for c in manifest["cases"] if c["dataset"]==dataset]
        matrix=np.array([[np.mean([r["scores"]["location"] for r in rows if r["case_id"]==cid and r["condition"]==condition])
                          for condition in CONDITIONS] for cid in ids])
        x=np.arange(1,len(ids)+1)
        for j,condition in enumerate(CONDITIONS):
            y=np.cumsum(matrix[:,j])/x*100
            ax.plot(x,y,marker="o",linestyle="-" if j==0 else "--",color=COLORS[condition],
                    label=LABELS[condition],ms=3,lw=3 if j==0 else 1.8)
            intervals=[cluster_interval(matrix[:n])[condition]["ci95"] for n in range(4,len(ids)+1)]
            bounds=np.array(intervals)*100
            ax.fill_between(x[3:],bounds[:,0],bounds[:,1],color=COLORS[condition],alpha=.10,lw=0)
        if np.array_equal(matrix[:,0],matrix[:,1]):
            ax.text(.98,.05,"Both conditions overlap",transform=ax.transAxes,ha="right",fontsize=9,color="#4B5563")
        ax.set_title(dataset,fontweight="bold");ax.set_xlabel("Unique tasks in frozen seeded order")
        ax.set_xlim(.75,len(ids)+.25);ax.set_ylim(0,105);ax.set_yticks(range(0,101,20));ax.set_xticks([1,4,8,12]);ax.grid(alpha=.18)
    np.atleast_1d(axes)[0].set_ylabel("Cumulative exact-location accuracy (%)")
    np.atleast_1d(axes)[-1].legend(frameon=False,fontsize=9)
    fig.suptitle("How the pilot estimate changes as tasks accumulate",fontsize=15,fontweight="bold",y=.98)
    fig.text(.5,.015,"Not a learning curve: no training or skill changes during this run.\nFixed order; 3 trials averaged per task. Shading: 95% task-cluster bootstrap, shown from n=4.",ha="center",fontsize=9,color="#4B5563")
    fig.tight_layout(rect=[0,.10,1,.92]);save(fig,"external-pilot-cumulative")
    print("Verified public tables and wrote PNG + SVG accuracy, paired-difference and cumulative plots.")


if __name__=="__main__": main()
