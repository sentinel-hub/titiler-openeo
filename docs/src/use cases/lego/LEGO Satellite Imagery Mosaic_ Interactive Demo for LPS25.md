# **LEGO Sentinel Mosaic: Interactive Demo for LPS25**

## **Overview**

We propose to create an interactive satellite imagery map of Europe using LEGO bricks at the CDSE booth during the ESA Living Planet Symposium 2025 (LPS25) in Vienna. This project will demonstrate the capabilities of openEO by TiTiler in a tangible, engaging way while encouraging participation from conference attendees.

## **Concept: Building Europe Mosaic Together, Brick by Brick**

The concept is simple yet impactful: conference attendees will collectively build a satellite imagery map of Europe using LEGO bricks (Sentinel 2 spring mosaic), with each participant contributing a small tile to the larger mosaic. This physical representation of Earth observation data symbolizes the collaborative nature of Earth science and showcases our data visualization capabilities in a memorable way.

![][image1]

## **Technical Implementation**

### **Map Specifications**

* **Final Size**: \~1m × \~1m mosaic map of Europe  
* **Resolution**: \~30 × \~30 tiles (\~900 tiles total)  
* **Brick Count**: Approximately 14,400 bricks (16 bricks per tile)  
* **Base Structure**: Gridded frame made of larger LEGO plates to support the individual tiles

![][image2]

*Preliminary test. Mosaic and colors to be refined*

### 

### **"Legofication" Process**

1. The [Sentinel-2 mosaic](https://browser.stac.dataspace.copernicus.eu/collections/sentinel-2-global-mosaics) of Europe from CDSE will be processed through our "[legofication](https://github.com/emmanuelmathot/titiler-openeo/blob/lego/titiler/openeo/processes/implementations/image.py#L369)" openEO process.  
2. The algorithm will:  
   * Downscale the satellite imagery to match our target resolution  
   * Map each pixel to the closest available LEGO brick color using CIEDE2000 color distance calculation  
   * Apply water mask enhancements (optional: using transparent bricks for water)  
   * Generate building instructions for each individual tile

![][image3]

### **User Experience Flow**

1. Attendee scans a QR code displayed at the CDSE booth  
2. QR code opens a web application on their mobile device  
3. User logs in with their CDSE credentials (this provides an opportunity to increase CDSE user registrations)  
4. User requests a tile to build (each user is limited to one tile)  
5. System assigns a unique tile and displays building instructions:  
   * Visual pattern showing 4×4 grid with specific colors  
   * List of required brick colors and quantities  
6. User collects the required bricks from organized bins at the booth  
7. User assembles their tile following the instructions  
8. User attaches their completed tile to the designated spot on the frame  
9. Optional: User's name or organization can be recorded on a digital map showing contribution areas

## **Technical Requirements**

### **Software Components**

1. **TiTiler-openEO Integration** \- The existing "legofication" process will be enhanced and optimized, especially to map color at best  
2. **Web Application** for tile assignment and instructions:  
   * Frontend: Mobile-friendly web interface with visual instructions  
   * Backend: API to assign tiles, track completion status, and generate instructions (actually, we will use the legofication process to do that. No need for a specific backend)  
   * Authentication: Integration with CDSE login  
   * QR Code Generation: Static QR code linking to the web application

### **Hardware/Physical Components**

1. **LEGO Bricks**: \~14,400 small plates (1×1) in various colors  
2. **Base Plates**: \~900 small plates (4×4) for individual tiles  
3. **Frame Structure**: Larger LEGO plates (16×16) to create the frame grid  
4. **Display Stand**: Support structure to hold the map vertically for visibility  
5. **Brick Organization System**: Sorted containers for easy access to different colored bricks  
6. **Instruction Area**: Small space for users to assemble their tiles before adding to the map

## **Logistics and Setup**

### **Space Requirements**

* **Wall/Display Space**: 1.2m × 1.2m area for mounting the LEGO map frame (slightly larger than the map itself to accommodate borders)  
* **Small Table/Counter Space**: Small area (approximately 0.5m × 1m) near the map for brick containers and assembly area  
* **Total Footprint**: Approximately 2m² total, which can be arranged to fit booth constraints

### **Duration and Timing**

The activity can be flexible based on CDSE's preferences:

* **Full Conference Option**: Run throughout all 5 days (June 23-27) at a slower pace, with approximately 180 tiles built per day  
* **Focused Option**: Concentrate on 2 specific high-traffic days (e.g., June 24-25) for more intensive engagement and completion but with the risk that the map could not be complete.

### **Pre-Conference Preparation**

1. Process satellite imagery and generate all tile instructions  
2. Pre-sort LEGO bricks by color into labeled containers  
3. Assemble the frame structure and mount it at the booth  
4. Test the web application and QR code functionality  
5. Print physical instructions as backup

### **During Conference**

1. Set up the empty frame at a visible location at the CDSE booth  
2. Position QR code and brief written instructions beside the map  
3. Place sorted brick containers nearby  
4. Have 1-2 team members available to assist occasionally (not required to be constantly staffed)

## **Budget**

### **Estimated Costs (Fully Covered by Development Seed)**

1. **LEGO Components**:  
   * 14,400 plate 1×1 bricks @ \~€0.05 each: \~€720  
   * 900 plate 4×4 @ \~€0.20 each: \~€180  
   * 60 plate 16×16 @ \~€4 each: \~€240  
   * Additional frame support bricks: \~€100  
   * Contingency (10%): \~€124  
   * **Subtotal**: \~€1,364  
2. **Web Application Development**: Internal resources  
3. **Signage and Printed Materials**: \~€100

**Total Estimated Budget**: \~€1,500

## **Benefits**

### **For Conference Attendees**

* Interactive, hands-on experience with Earth observation data visualization  
* Tangible contribution to a collaborative project  
* Introduction to CDSE platform capabilities through direct interaction

### **For CDSE Consortium**

* Engaging booth attraction that draws visitors  
* Demonstration of real CDSE data being used in innovative ways  
* Opportunity to increase user registrations  
* Showcase of collaborative partnerships with Development Seed

### **For Development Seed**

* Demonstration of TiTiler and openEO capabilities in an attention-grabbing format  
* Conversation starter about our technical expertise  
* Memorable presence at the conference beyond traditional booth setups  
* Alignment with our values of openness, collaboration and innovation

## **Implementation Timeline**

1. **April 2025**: Finalize technical details and begin web application development  
2. **May 2025**: Process satellite imagery, complete legofication algorithm refinements  
3. **Early June 2025**: Order LEGO components, test complete system  
4. **Week before LPS25**: Prepare physical components and finalize setup plan  
5. **LPS25 (June 23-27, 2025\)**: Implement at conference

## **Collaboration Opportunities**

While Development Seed will fully fund this initiative, we welcome collaboration with CDSE consortium members:

* Technical input on the mosaic imagery selection  
* Help promoting the activity to conference attendees  
* Suggestions for enhancing the user experience

## **Next Steps**

1. Confirm approval from CDSE consortium for implementation at their booth  
2. Begin technical development of the web application and enhanced legofication algorithm  
3. Define specific area of Europe to be represented

---

*This proposal was prepared by Development Seed for implementation at the ESA Living Planet Symposium 2025 in Vienna, Austria.*

