# Conference booth idea: Build a lego map from sentinel data using TiTiler

## Context

In growth team, we discussed about our presence at conferences and how we could make it engaging in case we have a booth.
This was initially put on the table for LPS25 and when I discussed with Olaf, we agreed that we did not want to simply stand in front of a poster with DevSeed logo and a computer.
We wanted to have something interactive and original presenting with our work and aligned with our values in the context of the conference.

## Idea: Lego Mosaic Map

*This is a very early draft idea and I need your feedback on it*

![image]

The pitch is simple: **Come and build a piece of a map using lego bricks!**

### How it works

- **Goal**: have a '*big big*' mosaic map of a region of the world (e.g. Europe) made of lego bricks.
- Each lego brick represents a **pixel**. Of course, the map cannot be high resolution so we need to downscale it.
- The map is made of **tiles** (of course!) and each tile is a 4x4 brick assembly.
- People would assemble the tiles and we would put them on a **gridded frame** made of the bigger plates.
- At the end of the conference, we would have a nice **mosaic map**

Original ✅
Interactive ✅

### Technical details

I added a small new process to titiler-openeo called `legofication` that tranforms a tile into the lego bricks assembly representation. It is a simple downscale and upscale process that adds features to the image to make it more lego-like.
Next, we need to map the colors of each brick to the real colors of the lego bricks and that's where it becomes tricky. It is difficult because there is no official color palette for lego bricks and after multiple emprical tests using the RGB colors of bricks, the result is not satisfying. I discovered this great article where a lego fan created a color palette based on the Pantone colors of the bricks then translated to HSL and RGB. I used this palette to create a dictionary of possible colors for the bricks and then using the colour-science lib, the tiler can compute the closest color to the real lego brick color using the CIEDE2000 color distance.
Add the end, we have a lego TiTiler that can generate lego brick assemblies. For now, only an image but it is easy to output a pdf or html file with the list of bricks to use and the assembly instructions.

Of course, the mockup you see here is a PoC using a sentinel-3 mosaic and these are not the real colors of the bricks. For LPS25, I would like to use the Sentinel-2 mosaic (spring time for the green and the snow). With the good color formula, I would expect the final result to be much prettier. Several improvements can be made to the legofication process to make the map more realistic. For example, we could add water mask and use transparent bricks for the water.

Presenting our work ✅

### How much? 🤑

It always comes down to the budget!
If we want to make something impactful and fun that may last some days before completion, we need a sufficiently big map.
Let's make an assumption for LPS25 knowing there was 5000 attendees in 2022 and that 20% of them would come to our booth and build a tile:

- At least a 1m x 1m map of Europe. The above test was made with a 144x154 bricks map (1.152mx1.232m) => 22 176 pixels bricks (plate 1x1). 😱
- 1386 tile support plates (4x4)
- ~90 frame support plates (16x16)

For the lego fan like me, you probably know bricklink as a broiker of lego bricks reseller. I made a quick search on the average price for the listed bricks based on quantity and available colors.

- 22 176 plate 1x1: 0.05€/brick : 1108€
- 1386 plate 4x4: 0.20€/brick : 277€
- 90 plate 16x16: 4€/brick : 360€
- additional bricks for mounting the frame: 100€

Total: 1845€ + 10% spare parts = 2029€

This is not cheap but there are multiple ways to reduce the cost:

- Ask a partner to collaborate on the project. E.g. Sinergise since we collaborate on titiler-open and that we would use their Sentinel-2 mosaic datasets. This is a good promotion for them.
- Make the tile paid. People would pay 3 or 4€ to build a tile (TOTAL: ~5500€) and we would donate the extra money to a cause aligned with our missions.

We can offer the map at the end of the conference to the conference host that they would display in their premises.

Aligned with our values ✅

## Feedback

This is a very early stage idea and I would like to have your feedback on it. Do you think it is a good idea? Do you have any suggestions to improve it? Do you think it is feasible? Do you have any other ideas? I would appreciate a lot also technical feedback on the legofication process, especially regarding the color mapping. Maybe the DevSeed designers have a better method to map the colors and make the map prettier!