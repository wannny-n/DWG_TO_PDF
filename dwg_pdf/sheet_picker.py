"""Выбор листов списком или секущей рамкой на схеме их расположения."""
import tkinter as tk
from tkinter import ttk


class SheetPicker(tk.Toplevel):
    def __init__(self,master,source,catalog,selected,on_accept):
        super().__init__(master)
        self.title(f'Листы для печати — {source.name}')
        self.geometry('1050x700')
        self.transient(master)
        self.grab_set()
        self.catalog=catalog
        self.selected=set(selected if selected is not None else range(1,len(catalog)+1))
        self.on_accept=on_accept
        self.columnconfigure(0,weight=1)
        self.rowconfigure(2,weight=1)
        ttk.Label(self,text='Отметьте нужные листы. На схеме можно выделить или исключить область рамкой.').grid(row=0,column=0,sticky='w',padx=12,pady=8)
        tools=ttk.Frame(self);tools.grid(row=1,column=0,sticky='w',padx=12)
        ttk.Button(tools,text='Выбрать все',command=lambda:self._all(True)).pack(side='left')
        ttk.Button(tools,text='Снять все',command=lambda:self._all(False)).pack(side='left',padx=6)
        self.add=tk.BooleanVar(value=True)
        ttk.Radiobutton(tools,text='Выделять рамкой',variable=self.add,value=True).pack(side='left',padx=10)
        ttk.Radiobutton(tools,text='Исключать рамкой',variable=self.add,value=False).pack(side='left')
        pane=ttk.Frame(self);pane.grid(row=2,column=0,sticky='nsew',padx=12,pady=8)
        pane.rowconfigure(0,weight=1);pane.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(pane,columns=('selected','number','title','format','size'),show='headings',selectmode='browse')
        for name,label,width in [('selected','Печатать',75),('number','№',45),('title','Лист / рамка',480),('format','Формат',100),('size','Размер, мм',150)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width)
        self.tree.grid(row=0,column=0,sticky='nsew')
        bar=ttk.Scrollbar(pane,command=self.tree.yview);bar.grid(row=0,column=1,sticky='ns');self.tree.configure(yscrollcommand=bar.set)
        for item in catalog:
            self.tree.insert('','end',iid=str(item['number']),values=('',item['number'],item['title'],item['format'],f"{item['width']:g} × {item['height']:g}"))
        self.tree.bind('<ButtonRelease-1>',self._toggle)
        self.tree.bind('<space>',self._toggle)
        self.canvas=tk.Canvas(self,height=190,bg='#f6f8fa',highlightthickness=1,highlightbackground='#ccd4dc')
        self.canvas.grid(row=3,column=0,sticky='ew',padx=12)
        ttk.Label(self,text='Схема расположения рамок в модели. Колесо мыши — масштаб; щелчок по рамке — переключить.').grid(row=4,column=0,sticky='w',padx=12)
        self.rectangles={}
        models=[i for i in catalog if i['model']]
        if models:
            minx=min(i['box'][0] for i in models);maxx=max(i['box'][2] for i in models)
            miny=min(i['box'][1] for i in models);maxy=max(i['box'][3] for i in models)
            scale=min(980/max(1,maxx-minx),150/max(1,maxy-miny))
            for item in models:
                x1,y1,x2,y2=item['box'];box=(15+(x1-minx)*scale,15+(maxy-y2)*scale,15+(x2-minx)*scale,15+(maxy-y1)*scale)
                rid=self.canvas.create_rectangle(*box,width=2)
                self.rectangles[rid]=item['number']
                self.canvas.create_text((box[0]+box[2])/2,(box[1]+box[3])/2,text=str(item['number']),fill='#172d45')
        else:
            self.canvas.create_text(400,70,text='Листы в пространстве листа выбираются в списке выше.')
        self.canvas.bind('<ButtonPress-1>',self._press)
        self.canvas.bind('<B1-Motion>',self._drag)
        self.canvas.bind('<ButtonRelease-1>',self._release)
        self.canvas.bind('<MouseWheel>',self._zoom)
        footer=ttk.Frame(self);footer.grid(row=5,column=0,sticky='ew',padx=12,pady=10)
        self.count=tk.StringVar()
        ttk.Label(footer,textvariable=self.count).pack(side='left')
        ttk.Button(footer,text='Применить',command=self._accept).pack(side='right')
        ttk.Button(footer,text='Отмена',command=self.destroy).pack(side='right',padx=8)
        self._refresh()

    def _all(self,state):
        self.selected=set(range(1,len(self.catalog)+1)) if state else set()
        self._refresh()

    def _refresh(self):
        for item in self.catalog:
            n=item['number'];self.tree.set(str(n),'selected','Да' if n in self.selected else '—')
        for rid,n in self.rectangles.items():
            self.canvas.itemconfigure(rid,outline='#24934a' if n in self.selected else '#a4a9b0',fill='#dff4e5' if n in self.selected else '#eee')
        self.count.set(f'Выбрано {len(self.selected)} из {len(self.catalog)}')

    def _toggle(self,event):
        iid=self.tree.identify_row(event.y) if hasattr(event,'y') and event.type!=tk.EventType.KeyPress else self.tree.focus()
        if iid:
            n=int(iid)
            if n in self.selected:self.selected.remove(n)
            else:self.selected.add(n)
            self._refresh()

    def _press(self,event):
        self.origin=(event.x,event.y)
        self.rubber=self.canvas.create_rectangle(event.x,event.y,event.x,event.y,outline='#3176c9',dash=(4,3))

    def _drag(self,event):
        self.canvas.coords(self.rubber,*self.origin,event.x,event.y)

    def _release(self,event):
        x,y=self.origin
        if abs(event.x-x)+abs(event.y-y)<5:
            for rid,n in self.rectangles.items():
                a,b,c,d=self.canvas.coords(rid)
                if a<=x<=c and b<=y<=d:
                    if n in self.selected:self.selected.remove(n)
                    else:self.selected.add(n)
                    break
        else:
            left,right=sorted((x,event.x));top,bottom=sorted((y,event.y))
            for rid,n in self.rectangles.items():
                a,b,c,d=self.canvas.coords(rid)
                if c>=left and a<=right and d>=top and b<=bottom:
                    if self.add.get():self.selected.add(n)
                    else:self.selected.discard(n)
        self.canvas.delete(self.rubber)
        self._refresh()

    def _zoom(self,event):
        scale=1.15 if event.delta>0 else 1/1.15
        self.canvas.scale('all',event.x,event.y,scale,scale)

    def _accept(self):
        self.on_accept(sorted(self.selected))
        self.destroy()
